"""Pins the real xdk response shape for fetch_tweet / post_reply.

The Phase 1 reviewer never exercised utils.py against the actual xdk Pydantic
models. Phase 4.5 added ``fetch_tweet`` and switched ``post_reply`` to the
``body=CreateRequest(...)`` shape — let's pin both against real SDK models so
future SDK bumps surface here, not on the deployed bot.

Specifically: ``xdk.posts.models`` defines ``Tweet = Any`` and
``Expansions = Any``, which means ``GetByIdResponse.model_validate({...}).data``
stays a *dict*, not a typed model. ``getattr(data, "text", None)`` therefore
returns the default and ``fetch_tweet`` silently returns None on every call —
the bot would post nothing in production.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")

from derad_agent.app import utils as utils_module  # noqa: E402


def _stub_x_client_with_get_by_id(response):
    """Build a stub Client whose .posts.get_by_id returns `response`."""
    client = MagicMock()
    client.posts = MagicMock()
    client.posts.get_by_id = MagicMock(return_value=response)
    return client


def test_fetch_tweet_unwraps_real_xdk_response_shape(monkeypatch):
    """A real GetByIdResponse must round-trip through fetch_tweet().

    The xdk SDK uses ``Tweet = Any`` and ``Expansions = Any``, so the Pydantic
    ``data`` and ``includes`` attributes are plain dicts at runtime. fetch_tweet
    must handle that, otherwise every production reply silently fails.
    """
    from xdk.posts.models import GetByIdResponse

    api_payload = {
        "data": {"id": "100", "text": "the parent tweet", "author_id": "42"},
        "includes": {"users": [{"id": "42", "username": "parent_user"}]},
    }
    response = GetByIdResponse.model_validate(api_payload)
    stub = _stub_x_client_with_get_by_id(response)
    monkeypatch.setattr(utils_module, "get_x_client", lambda: stub)

    snap = utils_module.fetch_tweet("100")

    # Currently fails: getattr(dict_instance, "text", None) is None, so
    # fetch_tweet returns None and the bot can never reply.
    assert snap is not None, (
        "fetch_tweet returned None for a well-formed GetByIdResponse — "
        "are you using getattr on a dict? xdk's Tweet alias is Any, so "
        "response.data stays a dict and needs subscript access."
    )
    assert snap.text == "the parent tweet"
    assert snap.author_id == "42"
    assert snap.author_username == "parent_user"


def test_post_reply_unwraps_real_xdk_create_response(monkeypatch):
    """post_reply must read the new reply id off CreateResponse.data.id."""
    from xdk.posts.models import CreateResponse

    response = CreateResponse.model_validate({"data": {"id": "999", "text": "hi"}})
    client = MagicMock()
    client.posts = MagicMock()
    client.posts.create = MagicMock(return_value=response)
    monkeypatch.setattr(utils_module, "get_x_client", lambda: client)

    new_id = utils_module.post_reply(parent_id="100", reply_text="hi")
    assert new_id == "999"
