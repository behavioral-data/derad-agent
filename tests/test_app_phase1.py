"""Phase-1 hardening tests for derad_agent.app.

These tests focus on the webhook handler, signature verification, dedup/rate
limit store, /info rendering, and the helpers that compose source-tweet text.
Pipeline internals (LLM calls, X API, index load) are mocked at the
module-attribute boundary so nothing needs network or the 712 MB index.

NOTE: env vars must be set BEFORE importing derad_agent.app.app — module
import calls _require_env("X_API_SECRET") and _require_env("SERVER_NAME").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import pytest

os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("DERAD_ALLOWED_AUTHOR_IDS", "111,222")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")
# Force a fresh in-memory dedup store on every test via a fixture below.

from derad_agent.app import app as app_module  # noqa: E402
from derad_agent.app import dedup as dedup_module  # noqa: E402


SECRET = os.environ["X_API_SECRET"]


def _sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return "sha256=" + base64.b64encode(digest).decode("utf-8")


@pytest.fixture
def client(monkeypatch):
    # Fresh store per test so dedup/rate-limit state doesn't leak across tests.
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())
    # The pipeline must not be invoked from these tests.
    def _no_pipeline(*a, **kw):
        raise AssertionError("process_mention should not run synchronously")
    monkeypatch.setattr(app_module, "process_mention", _no_pipeline)
    # Prevent the background thread by replacing threading.Thread.start.
    started = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    c._started_threads = started  # type: ignore[attr-defined]
    return c


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

class TestSignatureVerification:
    def test_accepts_valid_signature(self, client):
        body = json.dumps({"tweet_create_events": []}).encode()
        resp = client.post(
            "/mention-neutral",
            data=body,
            headers={
                "X-Twitter-Webhooks-Signature": _sign(body),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

    def test_rejects_missing_signature(self, client):
        resp = client.post("/mention-neutral", data=b"{}",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 403

    def test_rejects_wrong_signature(self, client):
        resp = client.post(
            "/mention-neutral",
            data=b"{}",
            headers={
                "X-Twitter-Webhooks-Signature": "sha256=AAAAAAAAAAAAAAAAAAAAAAAA",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    def test_rejects_signature_without_prefix(self, client):
        body = b"{}"
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
        bad = base64.b64encode(digest).decode()  # no 'sha256=' prefix
        resp = client.post(
            "/mention-neutral",
            data=body,
            headers={"X-Twitter-Webhooks-Signature": bad,
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 403

    def test_rejects_uppercase_prefix(self, client):
        # Twitter docs specify lowercase 'sha256='. Catch any future loosening.
        body = b"{}"
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
        bad = "SHA256=" + base64.b64encode(digest).decode()
        resp = client.post(
            "/mention-neutral",
            data=body,
            headers={"X-Twitter-Webhooks-Signature": bad,
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRC GET handshake
# ---------------------------------------------------------------------------

class TestCrcGet:
    def test_returns_token(self, client):
        resp = client.get("/mention-neutral?crc_token=abc123")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "response_token" in body
        assert body["response_token"].startswith("sha256=")

    def test_missing_crc_token_returns_400(self, client):
        resp = client.get("/mention-neutral")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Self-reply guard
# ---------------------------------------------------------------------------

def _signed_post(client, path, payload):
    body = json.dumps(payload).encode()
    return client.post(
        path,
        data=body,
        headers={"X-Twitter-Webhooks-Signature": _sign(body),
                 "Content-Type": "application/json"},
    )


class TestSelfReplyGuard:
    def test_blocks_self_reply_when_bot_id_set(self, client, monkeypatch):
        # Force a known bot id for this tone.
        monkeypatch.setitem(app_module.BOT_USER_ID_BY_TONE, "neutral", "999")
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "in_reply_to_status_id_str": "444",
                "user": {"id_str": "999"},
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200
        # No background thread should have been started.
        assert client._started_threads == []  # type: ignore[attr-defined]

    def test_fails_closed_when_bot_id_unset(self, client, monkeypatch):
        """When BOT_USER_ID_<TONE> is empty, the self-reply guard fails CLOSED.

        We cannot prove the tweet isn't authored by the bot itself, so the
        mention is skipped. Misconfiguration is loud (in logs) and safe (no
        unbounded reply chain).
        """
        monkeypatch.setitem(app_module.BOT_USER_ID_BY_TONE, "neutral", None)
        app_module.ALLOWED_AUTHOR_IDS.add("anyone")
        try:
            payload = {
                "tweet_create_events": [{
                    "id_str": "555",
                    "in_reply_to_status_id_str": "444",
                    "user": {"id_str": "anyone"},
                }]
            }
            resp = _signed_post(client, "/mention-neutral", payload)
            assert resp.status_code == 200
            assert client._started_threads == [], (  # type: ignore[attr-defined]
                "self-reply guard should fail-closed when BOT_USER_ID is unset"
            )
        finally:
            app_module.ALLOWED_AUTHOR_IDS.discard("anyone")

    def test_handles_missing_user_field(self, client):
        # tweet without 'user' should not crash; missing id_str is also benign.
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "in_reply_to_status_id_str": "444",
                # user missing entirely
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Author allow-list
# ---------------------------------------------------------------------------

class TestAllowList:
    def test_blocks_unregistered(self, client):
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "in_reply_to_status_id_str": "444",
                "user": {"id_str": "not-allowed"},
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200
        assert client._started_threads == []  # type: ignore[attr-defined]

    def test_allows_registered(self, client, monkeypatch):
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "in_reply_to_status_id_str": "444",
                "user": {"id_str": "111"},
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200
        assert len(client._started_threads) == 1  # type: ignore[attr-defined]

    def test_none_author_id_does_not_crash(self, client):
        # author_id is None when user.id_str is missing.
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "in_reply_to_status_id_str": "444",
                "user": {},
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200
        # With restrict_to_registered=true and a None author_id, must be blocked.
        assert client._started_threads == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Malformed / edge-case payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    def test_empty_payload(self, client):
        resp = _signed_post(client, "/mention-neutral", {})
        assert resp.status_code == 200

    def test_no_tweet_create_events(self, client):
        resp = _signed_post(client, "/mention-neutral", {"other_event": []})
        assert resp.status_code == 200

    def test_missing_parent_id(self, client):
        payload = {
            "tweet_create_events": [{
                "id_str": "555",
                "user": {"id_str": "111"},
                # no in_reply_to_status_id_str
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200
        assert client._started_threads == []  # type: ignore[attr-defined]

    def test_missing_mention_id(self, client):
        payload = {
            "tweet_create_events": [{
                "in_reply_to_status_id_str": "444",
                "user": {"id_str": "111"},
            }]
        }
        resp = _signed_post(client, "/mention-neutral", payload)
        assert resp.status_code == 200

    def test_non_json_body_still_safe(self, client):
        body = b"this is not json"
        resp = client.post(
            "/mention-neutral",
            data=body,
            headers={"X-Twitter-Webhooks-Signature": _sign(body),
                     "Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_tweet_events_with_wrong_type_does_not_crash(self, client):
        # tweet_create_events as a string instead of list — fail safe.
        body = json.dumps({"tweet_create_events": "oops"}).encode()
        resp = client.post(
            "/mention-neutral",
            data=body,
            headers={"X-Twitter-Webhooks-Signature": _sign(body),
                     "Content-Type": "application/json"},
        )
        # Should not 500. Either 200 or 400 is acceptable here.
        assert resp.status_code < 500


# ---------------------------------------------------------------------------
# Dedup + rate-limit store
# ---------------------------------------------------------------------------

class TestInMemoryStore:
    def test_claim_first_time_returns_true(self):
        store = dedup_module.InMemoryStore()
        assert store.claim("mention-1") is True

    def test_claim_duplicate_returns_false(self):
        store = dedup_module.InMemoryStore()
        store.claim("mention-1")
        assert store.claim("mention-1") is False

    def test_claim_expires_after_ttl(self):
        store = dedup_module.InMemoryStore()
        store.claim("mention-1", ttl_seconds=0)
        # ttl=0 means expiry == now; subsequent claim should succeed.
        time.sleep(0.01)
        assert store.claim("mention-1") is True

    def test_rate_limit_returns_count(self):
        store = dedup_module.InMemoryStore()
        assert store.hit_and_count("author:1", window_seconds=1) == 1
        assert store.hit_and_count("author:1", window_seconds=1) == 2
        assert store.hit_and_count("author:1", window_seconds=1) == 3

    def test_rate_limit_window_drops_old(self):
        store = dedup_module.InMemoryStore()
        store.hit_and_count("author:1", window_seconds=1)
        # Manually rewind timestamps so they fall outside the window.
        store._hits["author:1"] = [time.time() - 10.0]
        assert store.hit_and_count("author:1", window_seconds=1) == 1

    def test_hits_dict_evicts_idle_keys(self):
        """Idle keys are pruned on the next hit anywhere in the store.

        After global eviction lands, only the just-hit key should remain.
        """
        store = dedup_module.InMemoryStore()
        for i in range(500):
            store.hit_and_count(f"author:{i}", window_seconds=1)
        # Rewind every timestamp so every key falls outside the window.
        old = time.time() - 100.0
        for k in list(store._hits.keys()):
            store._hits[k] = [old]
        # Next call triggers global eviction across all keys.
        store.hit_and_count("author:0", window_seconds=1)
        assert set(store._hits.keys()) == {"author:0"}

    def test_dedup_eviction_runs(self):
        store = dedup_module.InMemoryStore()
        store.claim("expired", ttl_seconds=0)
        time.sleep(0.01)
        # Trigger another claim — eviction runs lazily inside claim().
        store.claim("fresh")
        assert "expired" not in store._dedup
        assert "fresh" in store._dedup


# ---------------------------------------------------------------------------
# /info template rendering — XSS surface
# ---------------------------------------------------------------------------

class TestInfoXss:
    def test_reflected_xss_via_reply_id(self, client):
        """BUG: reply_id is f-string interpolated then |safe-rendered.

        Crafting reply_id='"><script>alert(1)</script>' produces an executable
        script tag in the response. Reflected XSS.
        """
        # No need for the index; we want to inspect the reply_html block.
        payload = '"><script>alert(1)</script>'
        resp = client.get(
            "/info",
            query_string={"reply_id": payload, "tone": "neutral"},
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body, (
            "Reflected XSS: reply_id is rendered unescaped. "
            "Escape with markupsafe.escape() before interpolation, "
            "or use a Jinja template with autoescape on."
        )

    def test_reflected_xss_via_tweet_id(self, client, monkeypatch):
        """BUG: tweet_id is interpolated into notes_html.

        Even if no matching notes exist for the tweet id, the dummy index can
        return a note whose summary triggers the unescaped interpolation in
        generate_notes_html.
        """
        # Stub the index so it returns a controlled note for our crafted id.
        crafted = '"><script>xss()</script>'

        class _Idx:
            notes_by_tweet = {crafted: [{"note_id": "n1", "summary": "hello"}]}

        monkeypatch.setattr(app_module, "generate_notes_html",
                            app_module.generate_notes_html)  # keep real one
        from derad_agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "get_index", lambda: _Idx())
        resp = client.get(
            "/info",
            query_string={"reply_id": "1", "tone": "neutral",
                          "tweet_id": crafted, "note_id": "n1"},
        )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "<script>xss()</script>" not in body, (
            "tweet_id from the URL is interpolated unescaped into the fallback "
            "anchor in generate_notes_html (utils.py:127)."
        )

    def test_stored_xss_via_note_summary(self, client, monkeypatch):
        """BUG: note summary is interpolated into HTML with only URL regex
        rewriting. If the index contains a summary with HTML, it ships."""
        from derad_agent.app import utils as utils_module

        class _Idx:
            notes_by_tweet = {
                "tweet-1": [{"note_id": "note-1",
                             "summary": '<img src=x onerror="boom()">'}],
            }

        monkeypatch.setattr(utils_module, "get_index", lambda: _Idx())
        resp = client.get(
            "/info",
            query_string={"reply_id": "1", "tone": "neutral",
                          "tweet_id": "tweet-1", "note_id": "note-1"},
        )
        body = resp.get_data(as_text=True)
        assert 'onerror="boom()"' not in body, (
            "Stored XSS surface: note summaries are not HTML-escaped before "
            "embedding in /info. Trust depends entirely on the upstream feed."
        )


# ---------------------------------------------------------------------------
# Helper: _fit_sources_text edge cases
# ---------------------------------------------------------------------------

class TestFitSourcesText:
    def test_short_sources_fit(self):
        text = app_module._fit_sources_text(
            ["https://a.example/1", "https://a.example/2"],
            "https://test.local/info?x=1",
        )
        assert "Sources:" in text
        assert "More Info:" in text
        assert "a.example/1" in text
        assert "a.example/2" in text

    def test_sources_none(self):
        text = app_module._fit_sources_text(None, "https://test.local/info")  # type: ignore[arg-type]
        assert "Sources:" in text
        assert "More Info: https://test.local/info" in text

    def test_sources_empty(self):
        text = app_module._fit_sources_text([], "https://test.local/info")
        assert "Sources:" in text
        assert "More Info: https://test.local/info" in text

    def test_drops_urls_until_fits(self):
        # Construct a deliberately huge list of long URLs.
        urls = [f"https://example.com/very/long/path/segment/{i}" * 4
                for i in range(20)]
        info_url = "https://test.local/info?id=1"
        text = app_module._fit_sources_text(urls, info_url, limit=280)
        assert len(text) <= 280, f"got {len(text)} chars"
        assert "More Info:" in text

    def test_keeps_info_url_even_if_alone_too_long(self):
        # Info URL alone exceeds the limit; function still returns it.
        long_info = "https://test.local/" + "x" * 500
        text = app_module._fit_sources_text(["https://a.example/1"], long_info, limit=280)
        # All URLs dropped, just header + info URL — may exceed 280 but should not crash.
        assert "More Info:" in text


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

class TestHealthz:
    def test_ok_without_index_loaded(self, client, monkeypatch):
        from derad_agent.app import utils as utils_module
        monkeypatch.setattr(utils_module, "index_loaded", lambda: False)
        monkeypatch.setattr(app_module, "index_loaded", lambda: False)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["index_loaded"] is False


# ---------------------------------------------------------------------------
# Duplicate delivery (X retries the same mention_id)
# ---------------------------------------------------------------------------

class TestDuplicateDelivery:
    def test_second_delivery_dropped(self, client):
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        payload = {
            "tweet_create_events": [{
                "id_str": "777",
                "in_reply_to_status_id_str": "444",
                "user": {"id_str": "111"},
            }]
        }
        first = _signed_post(client, "/mention-neutral", payload)
        second = _signed_post(client, "/mention-neutral", payload)
        assert first.status_code == 200
        assert second.status_code == 200
        assert len(client._started_threads) == 1, (  # type: ignore[attr-defined]
            "expected dedup to drop the duplicate delivery"
        )


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_burst_above_limit_drops_extras(self, client):
        app_module.ALLOWED_AUTHOR_IDS.add("111")
        # 5 distinct mentions from the same author in <1s.
        for i in range(5):
            payload = {
                "tweet_create_events": [{
                    "id_str": f"m{i}",
                    "in_reply_to_status_id_str": "444",
                    "user": {"id_str": "111"},
                }]
            }
            resp = _signed_post(client, "/mention-neutral", payload)
            assert resp.status_code == 200
        # RATE_LIMIT_PER_SEC defaults to 3; allow some flake.
        accepted = len(client._started_threads)  # type: ignore[attr-defined]
        assert accepted <= app_module.RATE_LIMIT_PER_SEC, (
            f"expected <= {app_module.RATE_LIMIT_PER_SEC} accepted, got {accepted}"
        )
        assert accepted >= 1, "at least one mention should have been accepted"
