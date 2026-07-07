"""Tests for agent.cli.collect_replies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent.app.events import BotReplyReply, MentionEvent, InMemoryEventsStore
from agent.app import events as events_module
from agent.cli.collect_replies import (
    SEARCH_RECENT_HORIZON,
    _collect_one,
    _select_candidates,
    main,
)


def _utc(days_ago=0, **kwargs):
    base = datetime(2026, 5, 20, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return base.replace(**kwargs) if kwargs else base


def _make_pages(tweets: list[dict], users: list[dict] | None = None):
    """Build a fake search_recent generator: yields page objects with .data/.includes.

    Mirrors the real xdk contract — posts.search_recent returns a generator of
    page objects, each carrying a slice of results, not a single response.
    """
    page = MagicMock()
    page.data = tweets
    page.includes = {"users": users or []}
    return iter([page])


def _replied_to(target_id: str) -> list[dict]:
    """referenced_tweets entry marking a tweet as a reply to target_id."""
    return [{"type": "replied_to", "id": target_id}]


@pytest.fixture(autouse=True)
def fresh_events_store(monkeypatch):
    store = InMemoryEventsStore()
    monkeypatch.setattr(events_module, "_default_store", store)
    return store


class TestCollectOne:
    def test_happy_path_direct_reply(self, monkeypatch):
        """A tweet whose referenced_tweets replies to the bot is written."""
        tweet = {
            "id": "rr1",
            "author_id": "user42",
            "text": "interesting take",
            "referenced_tweets": _replied_to("bot99"),
            "public_metrics": {"like_count": 3},
        }
        user = {"id": "user42", "username": "alice"}

        fake_client = MagicMock()
        fake_client.posts.search_recent.return_value = _make_pages([tweet], [user])
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        written_replies: list[BotReplyReply] = []
        monkeypatch.setattr(
            "agent.cli.collect_replies.log_reply_reply",
            lambda r: written_replies.append(r),
        )

        count = _collect_one("bot99", "neutral", mention_id="m1", parent_id="p1")
        assert count == 1
        assert written_replies[0].reply_tweet_id == "rr1"
        assert written_replies[0].author_username == "alice"
        assert written_replies[0].like_count == 3
        assert written_replies[0].tone == "neutral"
        assert written_replies[0].mention_id == "m1"

    def test_non_direct_reply_is_filtered(self, monkeypatch):
        """A tweet in the conversation that doesn't reply to the bot tweet is skipped."""
        tweet = {
            "id": "other1",
            "author_id": "user99",
            "text": "replying to someone else",
            "referenced_tweets": _replied_to("some_other_tweet"),
            "public_metrics": {"like_count": 0},
        }

        fake_client = MagicMock()
        fake_client.posts.search_recent.return_value = _make_pages([tweet])
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )
        monkeypatch.setattr("agent.cli.collect_replies.log_reply_reply", MagicMock())

        count = _collect_one("bot99", "neutral", mention_id=None, parent_id="p1")
        assert count == 0

    def test_bot_self_reply_excluded_by_author(self, monkeypatch):
        """A reply authored by the bot itself is not logged as a bystander reply."""
        tweets = [
            {"id": "link1", "author_id": "bot_uid", "text": "dossier: ...",
             "referenced_tweets": _replied_to("bot99"), "public_metrics": {}},
            {"id": "rr1", "author_id": "user42", "text": "real reply",
             "referenced_tweets": _replied_to("bot99"), "public_metrics": {}},
        ]
        fake_client = MagicMock()
        fake_client.posts.search_recent.return_value = _make_pages(
            tweets, [{"id": "user42", "username": "alice"}]
        )
        monkeypatch.setattr("agent.cli.collect_replies.get_x_client", lambda: fake_client)
        written: list[BotReplyReply] = []
        monkeypatch.setattr("agent.cli.collect_replies.log_reply_reply", written.append)

        count = _collect_one("bot99", "neutral", mention_id="m1", parent_id="p1",
                             bot_user_id="bot_uid")
        assert count == 1
        assert [w.reply_tweet_id for w in written] == ["rr1"]

    def test_link_self_reply_excluded_by_id_when_bot_id_unset(self, monkeypatch):
        """Backstop: even with no BOT_USER_ID, the known link_reply_id is dropped."""
        tweets = [
            {"id": "link1", "author_id": "bot_uid", "text": "dossier: ...",
             "referenced_tweets": _replied_to("bot99"), "public_metrics": {}},
            {"id": "rr1", "author_id": "user42", "text": "real reply",
             "referenced_tweets": _replied_to("bot99"), "public_metrics": {}},
        ]
        fake_client = MagicMock()
        fake_client.posts.search_recent.return_value = _make_pages(
            tweets, [{"id": "user42", "username": "alice"}]
        )
        monkeypatch.setattr("agent.cli.collect_replies.get_x_client", lambda: fake_client)
        monkeypatch.delenv("BOT_USER_ID", raising=False)
        written: list[BotReplyReply] = []
        monkeypatch.setattr("agent.cli.collect_replies.log_reply_reply", written.append)

        count = _collect_one("bot99", "neutral", mention_id="m1", parent_id="p1",
                             link_reply_id="link1", bot_user_id=None)
        assert count == 1
        assert [w.reply_tweet_id for w in written] == ["rr1"]

    def test_api_exception_returns_zero(self, monkeypatch):
        """Network error returns 0 without raising."""
        fake_client = MagicMock()
        fake_client.posts.search_recent.side_effect = RuntimeError("timeout")
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        count = _collect_one("bot99", "neutral", mention_id=None, parent_id="p1")
        assert count == 0

    def test_skips_when_no_parent_id(self, monkeypatch):
        """Without parent_id there's no searchable conversation, so it skips without an API call."""
        fake_client = MagicMock()
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        count = _collect_one("bot99", "neutral", mention_id=None, parent_id=None)
        assert count == 0
        fake_client.posts.search_recent.assert_not_called()

    def test_uses_conversation_query_when_parent_present(self, monkeypatch):
        """With parent_id the query uses conversation_id:."""
        captured: list[str] = []

        fake_client = MagicMock()
        fake_client.posts.search_recent.side_effect = lambda **kw: (
            captured.append(kw.get("query", "")) or _make_pages([])
        )
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        _collect_one("bot99", "neutral", mention_id=None, parent_id="parent123")
        assert captured[0] == "conversation_id:parent123"

    def test_uses_conversation_id_over_parent_id_when_present(self, monkeypatch):
        """A mid-thread claim's conversation_id (the true thread root) differs
        from parent_id (the immediately-replied-to tweet). Searching on
        parent_id there hits the wrong (sub-)conversation and silently
        collects zero bystander replies."""
        captured: list[str] = []

        fake_client = MagicMock()
        fake_client.posts.search_recent.side_effect = lambda **kw: (
            captured.append(kw.get("query", "")) or _make_pages([])
        )
        monkeypatch.setattr(
            "agent.cli.collect_replies.get_x_client",
            lambda: fake_client,
        )

        _collect_one(
            "bot99", "neutral", mention_id=None,
            parent_id="immediate_parent", conversation_id="thread_root",
        )
        assert captured[0] == "conversation_id:thread_root"


class TestSelectCandidates:
    """Coverage for the candidate filter used by main(): conversation_id
    round-trips through to collect_one, legacy rows without one fall back to
    parent_id (handled by _collect_one itself), and replies whose capture
    window has aged past search_recent's ~7-day horizon are dropped and
    logged instead of being retried forever."""

    def _event(self, **overrides):
        base = dict(
            mention_id="m1", parent_id="p1", author_id="a1", tone="neutral",
            received_at_utc=_utc(days_ago=20),
            reply_posted_utc=_utc(days_ago=20),
            reply_id="r1",
        )
        base.update(overrides)
        return MentionEvent(**base)

    def test_conversation_id_flows_through_to_candidate(self, fresh_events_store):
        fresh_events_store.write_event(self._event(
            reply_id="r1", conversation_id="c1", reply_posted_utc=_utc(days_ago=4),
        ))
        candidates = _select_candidates(fresh_events_store, _utc(days_ago=0))
        assert len(candidates) == 1
        reply_id, _tone, _mention_id, _parent_id, _link_reply_id, conversation_id = candidates[0]
        assert reply_id == "r1"
        assert conversation_id == "c1"

    def test_legacy_row_without_conversation_id_yields_none(self, fresh_events_store):
        fresh_events_store.write_event(self._event(
            reply_id="r2", conversation_id=None, reply_posted_utc=_utc(days_ago=4),
        ))
        candidates = _select_candidates(fresh_events_store, _utc(days_ago=0))
        assert len(candidates) == 1
        assert candidates[0][0] == "r2"
        assert candidates[0][5] is None  # _collect_one falls back to parent_id itself

    def test_reply_aged_past_search_horizon_is_dropped_and_logged(self, fresh_events_store, caplog):
        fresh_events_store.write_event(self._event(
            reply_id="stale1", reply_posted_utc=_utc(days_ago=10),
        ))
        with caplog.at_level("WARNING", logger="agent.cli.collect_replies"):
            candidates = _select_candidates(fresh_events_store, _utc(days_ago=0))
        assert candidates == []
        assert "stale1" in caplog.text
        assert "aged out" in caplog.text

    def test_reply_within_horizon_is_returned(self, fresh_events_store):
        fresh_events_store.write_event(self._event(
            reply_id="fresh1", reply_posted_utc=_utc(days_ago=4),
        ))
        candidates = _select_candidates(fresh_events_store, _utc(days_ago=0))
        assert [c[0] for c in candidates] == ["fresh1"]

    def test_reply_exactly_at_horizon_boundary_is_dropped(self, fresh_events_store):
        """age >= SEARCH_RECENT_HORIZON must be excluded, not just age > horizon —
        at exactly the boundary search_recent's window no longer covers the
        reply's post time."""
        fresh_events_store.write_event(self._event(
            reply_id="boundary1",
            reply_posted_utc=_utc(days_ago=0) - SEARCH_RECENT_HORIZON,
        ))
        candidates = _select_candidates(fresh_events_store, _utc(days_ago=0))
        assert candidates == []


class TestCollectRepliesMain:
    def test_no_candidates_exits_early(self, monkeypatch, fresh_events_store):
        """With no replies in the 3-day window main() logs and returns."""
        # Store has no events so iter_reply_ids() is empty
        monkeypatch.setattr(events_module, "_default_store", fresh_events_store)
        log_calls: list[str] = []
        monkeypatch.setattr(
            "agent.cli.collect_replies.logger",
            MagicMock(info=lambda msg, *a: log_calls.append(msg % a if a else msg)),
        )
        main()
        assert any("nothing to collect" in m for m in log_calls)
