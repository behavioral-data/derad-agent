"""Regression tests for the dossier-link self-reply outcome split.

When the main fact-check reply posts but the follow-up link self-reply fails,
the event row must surface that gap via outcome='replied_no_link' (and a None
``link_reply_id``) — otherwise the reliability hole is invisible to analytics.

Companion tests in tests/test_events.py cover the thread shape of the two
posts; here we focus narrowly on outcome/link_reply_id under the two cases
the code path branches on.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

# Env vars must be set BEFORE importing app.py (module-load _require_env).
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import events as events_module  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def fake_events_store():
    store = events_module.InMemoryEventsStore()
    events_module.reset_store(store)
    yield store
    events_module.reset_store(None)


def _drive_pipeline(monkeypatch, fake_events_store, post_reply_returns):
    """Invoke process_mention with a stubbed main + link post_reply sequence.

    ``post_reply_returns`` is consumed in order: [main_reply, link_attempt_1, link_retry].
    """
    monkeypatch.setattr(app_module, "DRY_RUN", False)
    # No real sleep — retry path still calls time.sleep(1.0).
    monkeypatch.setattr(app_module.time, "sleep", lambda *_a, **_kw: None)

    from agent.app.utils import TweetSnapshot
    from agent.app import utils as utils_module
    snap = TweetSnapshot(
        text="Mail-in voting causes fraud.",
        author_id="12345",  # third-party parent; must differ from BOT_USER_ID
        author_username="parent_user",
    )
    monkeypatch.setattr(utils_module, "fetch_tweet", lambda *a, **kw: snap)
    monkeypatch.setattr(app_module, "fetch_tweet", lambda *a, **kw: snap)
    monkeypatch.setattr(app_module, "generate_reply", lambda **kw: {
        "text": "Here are the facts.",
        "sources": ["https://a.example"],
        "verdict_label": "Refuted",
        "action": "verify",
        "action_outcome": "verified_refuted",
        "queries": ["q1"],
    })

    calls: list[dict] = []

    def _post(parent_id, reply_text):
        calls.append({"parent_id": parent_id, "reply_text": reply_text})
        return post_reply_returns[len(calls) - 1]

    monkeypatch.setattr(app_module, "post_reply", _post)

    tweet = {
        "id_str": "555",
        "in_reply_to_status_id_str": "444",
        "user": {"id_str": "111", "screen_name": "alice"},
    }
    app_module.process_mention("neutral", tweet, _now())
    assert fake_events_store.events, "process_mention should write an event row"
    return calls, fake_events_store.events[-1]


def test_link_reply_success_records_replied_and_link_reply_id(monkeypatch, fake_events_store):
    """Happy path: link self-reply succeeds on the first attempt.

    Outcome is 'replied' and link_reply_id is populated from the second post.
    No retry happens (only two post_reply calls).
    """
    calls, ev = _drive_pipeline(
        monkeypatch, fake_events_store,
        post_reply_returns=["MAIN_ID", "LINK_ID"],
    )
    assert len(calls) == 2, "no retry expected when the link self-reply succeeds"
    assert ev.outcome == "replied"
    assert ev.reply_id == "MAIN_ID"
    assert ev.link_reply_id == "LINK_ID"


def test_link_reply_failure_both_attempts_records_replied_no_link(monkeypatch, fake_events_store):
    """Failure path: link self-reply returns None on both the initial attempt
    and the one retry. Outcome flips to 'replied_no_link' and link_reply_id
    stays None so analytics can spot the gap.
    """
    calls, ev = _drive_pipeline(
        monkeypatch, fake_events_store,
        post_reply_returns=["MAIN_ID", None, None],
    )
    assert len(calls) == 3, "expected main reply + initial link attempt + 1 retry"
    assert ev.outcome == "replied_no_link"
    assert ev.reply_id == "MAIN_ID"
    assert ev.link_reply_id is None
