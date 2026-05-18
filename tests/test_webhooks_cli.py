"""Tests for the derad-webhooks CLI.

The xdk SDK is stubbed at the boundary (`get_x_client`) so no live X API
credentials or network access are needed.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import MagicMock

import pytest


def _fake_response(data, errors=None):
    """Mock a nested response model carrying `data` + optional `errors`."""
    resp = MagicMock()
    resp.data = data
    resp.errors = errors
    return resp


def _http_error(status: int, body: str = "") -> "requests.HTTPError":
    """Construct a requests.HTTPError shaped the way xdk raises it."""
    import requests
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    return requests.HTTPError(f"{status} error", response=response)


def _build_client(**method_responses):
    """Construct a MagicMock client wired with the given resp objects."""
    client = MagicMock()
    for path, resp in method_responses.items():
        owner_path, _, method = path.rpartition(".")
        owner = client
        for part in owner_path.split("."):
            owner = getattr(owner, part)
        setattr(owner, method, MagicMock(return_value=resp))
    return client


@pytest.fixture
def patched_client(monkeypatch):
    """Replace get_x_client so each test can stub the SDK return values."""
    from derad_agent.cli import webhooks as cli
    holder = {"client": None}

    def _factory(tone):
        return holder["client"]
    monkeypatch.setattr(cli, "_get_client", _factory)
    return holder


def _run(argv, capture=True):
    from derad_agent.cli import webhooks as cli
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# ── Read-only / introspection ───────────────────────────────────────────────

class TestMe:
    def test_emits_user_data(self, patched_client):
        patched_client["client"] = _build_client(**{
            "users.get_me": _fake_response({"id": "999", "username": "nellie_bot"}),
        })
        code, out, err = _run(["me", "--tone", "neutral"])
        assert code == 0, err
        body = json.loads(out)
        assert body == {"id": "999", "username": "nellie_bot"}

    def test_propagates_http_error_to_stderr(self, patched_client):
        # xdk raises requests.HTTPError on 4xx/5xx via raise_for_status().
        client = MagicMock()
        client.users.get_me = MagicMock(side_effect=_http_error(403, "Forbidden: bad creds"))
        patched_client["client"] = client
        code, out, err = _run(["me", "--tone", "neutral"])
        assert code == 1
        assert "HTTP 403" in err
        assert "Forbidden: bad creds" in err

    def test_surfaces_errors_field_on_2xx(self, patched_client):
        # 2xx response that still carries an `errors` list — must exit non-zero
        # so the runbook fails loudly instead of swallowing the problem.
        client = _build_client(**{
            "users.get_me": _fake_response(None, errors=[{"title": "OAuth1 token rejected"}]),
        })
        patched_client["client"] = client
        code, out, err = _run(["me", "--tone", "neutral"])
        assert code == 1
        assert "OAuth1 token rejected" in err


# ── Webhook lifecycle ───────────────────────────────────────────────────────

class TestRegister:
    def test_register_emits_real_create_response_shape(self, patched_client):
        """Pins the production bug: xdk CreateResponse has no `.data` attr.

        The CLI's ``cmd_register`` calls ``_emit(resp.data)``, but the real
        CreateResponse model fields are flat (``id``, ``url``, ``valid``,
        ``created_at``) — no ``data`` field. Against the real SDK this raises
        ``AttributeError`` and ``derad-webhooks register`` is dead on arrival.
        """
        from xdk.webhooks.models import CreateResponse
        real_resp = CreateResponse(
            id="abc", url="https://example/mention-neutral",
            valid=True, created_at="2024-01-01T00:00:00Z",
        )
        client = MagicMock()
        client.webhooks.create = MagicMock(return_value=real_resp)
        patched_client["client"] = client

        code, out, err = _run([
            "register", "--tone", "neutral",
            "--url", "https://example/mention-neutral",
        ])
        assert code == 0, f"register failed: stderr={err!r}"
        body = json.loads(out)
        assert body.get("id") == "abc"
        assert body.get("url") == "https://example/mention-neutral"
        assert body.get("valid") is True

    def test_register_calls_create_with_correct_url(self, patched_client):
        webhook_data = {"id": "abc", "url": "https://example/mention-neutral", "valid": True}
        client = _build_client(**{
            "webhooks.create": _fake_response(webhook_data),
        })
        patched_client["client"] = client

        code, out, _ = _run([
            "register", "--tone", "neutral",
            "--url", "https://example/mention-neutral",
        ])
        assert code == 0
        # The body passed to webhooks.create should be a CreateRequest carrying our URL.
        called_with = client.webhooks.create.call_args.kwargs["body"]
        from xdk.webhooks.models import CreateRequest
        assert isinstance(called_with, CreateRequest)
        assert called_with.url == "https://example/mention-neutral"
        assert json.loads(out) == webhook_data

    def test_register_propagates_http_error(self, patched_client):
        client = MagicMock()
        client.webhooks.create = MagicMock(
            side_effect=_http_error(403, "CRC handshake failed"),
        )
        patched_client["client"] = client
        code, _, err = _run([
            "register", "--tone", "agreeable",
            "--url", "https://example/mention-agreeable",
        ])
        assert code == 1
        assert "HTTP 403" in err and "CRC handshake failed" in err


class TestList:
    def test_emits_webhooks_data(self, patched_client):
        webhooks_data = [{"id": "1", "url": "https://example/mention-neutral"}]
        patched_client["client"] = _build_client(**{
            "webhooks.get": _fake_response(webhooks_data),
        })
        code, out, _ = _run(["list", "--tone", "neutral"])
        assert code == 0
        assert json.loads(out) == webhooks_data


class TestValidate:
    def test_calls_validate_with_webhook_id(self, patched_client):
        client = _build_client(**{"webhooks.validate": _fake_response({"valid": True})})
        patched_client["client"] = client
        code, _, _ = _run(["validate", "--tone", "neutral", "--webhook-id", "abc"])
        assert code == 0
        client.webhooks.validate.assert_called_once_with(webhook_id="abc")


class TestDelete:
    def test_emits_deleted_id(self, patched_client):
        client = _build_client(**{"webhooks.delete": _fake_response({})})
        patched_client["client"] = client
        code, out, _ = _run(["delete", "--tone", "neutral", "--webhook-id", "abc"])
        assert code == 0
        client.webhooks.delete.assert_called_once_with(webhook_id="abc")
        assert json.loads(out) == {"deleted": "abc"}


# ── Subscriptions ───────────────────────────────────────────────────────────

class TestSubscribe:
    def test_calls_create_subscription(self, patched_client):
        client = _build_client(**{
            "account_activity.create_subscription": _fake_response({}),
        })
        patched_client["client"] = client

        code, out, _ = _run([
            "subscribe", "--tone", "satirical", "--webhook-id", "xyz",
        ])
        assert code == 0
        client.account_activity.create_subscription.assert_called_once_with(webhook_id="xyz")
        body = json.loads(out)
        assert body["subscribed_tone"] == "satirical"
        assert body["webhook_id"] == "xyz"


class TestUnsubscribe:
    def test_calls_delete_subscription(self, patched_client):
        client = _build_client(**{
            "account_activity.delete_subscription": _fake_response({}),
        })
        patched_client["client"] = client

        code, _, _ = _run([
            "unsubscribe", "--tone", "neutral",
            "--webhook-id", "xyz", "--user-id", "999",
        ])
        assert code == 0
        client.account_activity.delete_subscription.assert_called_once_with(
            webhook_id="xyz", user_id="999",
        )


class TestSubscriptions:
    def test_emits_subscription_list(self, patched_client):
        subs = {"subscriptions": [{"user_id": "999"}]}
        patched_client["client"] = _build_client(**{
            "account_activity.get_subscriptions": _fake_response(subs),
        })
        code, out, _ = _run([
            "subscriptions", "--tone", "neutral", "--webhook-id", "xyz",
        ])
        assert code == 0
        assert json.loads(out) == subs


# ── Validation ──────────────────────────────────────────────────────────────

class TestArgs:
    def test_unknown_tone_rejected(self, patched_client, monkeypatch):
        # argparse exits with code 2 on choice errors.
        with pytest.raises(SystemExit) as e:
            _run(["me", "--tone", "bogus"])
        assert e.value.code == 2

    def test_missing_url_rejected(self, patched_client):
        with pytest.raises(SystemExit) as e:
            _run(["register", "--tone", "neutral"])
        assert e.value.code == 2
