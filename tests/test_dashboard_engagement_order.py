"""Ordering tests for /api/engagement when timestamps mix `Z` and `+00:00` suffixes.

The InMemoryEventsStore emits ISO timestamps via ``datetime.isoformat()``
(``...+00:00``), while TablesEventsStore can emit the Azure SDK's isoformat
(``...Z``). Lexicographic compare on the raw strings is NOT order-correct
across those formats, so the dashboard endpoint and its helpers must parse
before compare.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("BOT_USER_ID", "999")

from agent.app import app as app_module  # noqa: E402
from agent.app import events as events_module  # noqa: E402


# Crafted so lexicographic order disagrees with chronological order:
#   - `TS_EARLIER_Z`  = 2026-05-27T12:00:00.000000Z      (chron-earlier)
#   - `TS_LATER_OFFSET` = 2026-05-27T12:00:00.000001+00:00  (chron-later, by 1 µs)
# Strings match through "2026-05-27T12:00:00", then differ at position 19:
#   `Z` (0x5A) vs `.` (0x2E). `Z` > `.`, so lex compare would (wrongly) say
#   the chron-EARLIER timestamp is greater.
TS_EARLIER_Z = "2026-05-27T12:00:00Z"
TS_LATER_OFFSET = "2026-05-27T12:00:00.000001+00:00"


def test_parse_iso_utc_handles_both_suffixes_correctly():
    """The helper must order by chronology, not by lex on the raw string."""
    later = events_module.parse_iso_utc(TS_LATER_OFFSET)
    earlier = events_module.parse_iso_utc(TS_EARLIER_Z)
    # Sanity: lex compare on the raw strings would (wrongly) say earlier > later.
    assert TS_EARLIER_Z > TS_LATER_OFFSET
    # Chronologically the `+00:00` one is later.
    assert later > earlier
    # Returned datetimes are tz-aware UTC.
    assert later.tzinfo is not None
    assert earlier.tzinfo is not None


def test_parse_iso_utc_missing_sorts_last():
    sentinel_min = datetime.min.replace(tzinfo=timezone.utc)
    assert events_module.parse_iso_utc(None) == sentinel_min
    assert events_module.parse_iso_utc("") == sentinel_min
    assert events_module.parse_iso_utc("not-a-timestamp") == sentinel_min


def test_parse_iso_utc_accepts_datetime():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Naive datetime is treated as UTC.
    assert events_module.parse_iso_utc(naive) == aware
    # Already aware datetime passes through.
    assert events_module.parse_iso_utc(aware) == aware


class _MixedSuffixStore:
    """Minimal store stub returning rows with mixed `Z` / `+00:00` suffixes.

    Exercises /api/engagement ordering under the exact condition the real
    backends produce in prod (two different ISO renderings of the same field).
    """

    def __init__(self, snaps, views, replies=None):
        self._snaps = snaps
        self._views = views
        self._replies = replies or []

    def list_recent_engagements(self, _limit):
        return list(self._snaps)

    def list_recent_reply_replies(self, _limit):
        return list(self._replies)

    def list_recent_info_views(self, _limit):
        return list(self._views)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_engagement_endpoint_sorts_by_chronological_order(monkeypatch, client):
    """Mixed-suffix snapshots must order by chron time, not lex order.

    R_LATER has the chronologically-later poll time but its ISO string ends
    in `Z`, while R_EARLIER's earlier time ends in `+00:00`. Lex compare
    would put R_EARLIER first (its `+00:00` string is lex-greater); chron
    order puts R_LATER first.
    """
    snaps = [
        {"reply_id": "R_LATER", "tone": "neutral",
         "polled_at_utc": TS_LATER_OFFSET, "like_count": 1},
        {"reply_id": "R_EARLIER", "tone": "neutral",
         "polled_at_utc": TS_EARLIER_Z, "like_count": 1},
    ]
    monkeypatch.setattr(
        events_module, "get_store",
        lambda: _MixedSuffixStore(snaps, views=[]),
    )

    data = client.get("/api/engagement").get_json()
    order = [r["reply_id"] for r in data["by_reply"]]
    # Sort is reverse=True (most-recent first), so the chronologically-later row leads.
    assert order == ["R_LATER", "R_EARLIER"], (
        f"Engagement table not sorted chronologically: got {order}"
    )


def test_engagement_endpoint_click_only_sort_uses_chronology(monkeypatch, client):
    """Click-only rows (no snapshot) fall back to latest InfoView ts.

    Same trap: viewed_at_utc strings can be `Z`-suffixed or `+00:00`-suffixed
    depending on the backend, so the click-only fallback must parse before
    compare too.
    """
    views = [
        {"reply_id": "R_LATER", "viewed_at_utc": TS_LATER_OFFSET,
         "tone": "neutral", "is_bot": False},
        {"reply_id": "R_EARLIER", "viewed_at_utc": TS_EARLIER_Z,
         "tone": "neutral", "is_bot": False},
    ]
    monkeypatch.setattr(
        events_module, "get_store",
        lambda: _MixedSuffixStore(snaps=[], views=views),
    )

    data = client.get("/api/engagement").get_json()
    order = [r["reply_id"] for r in data["by_reply"]]
    assert order == ["R_LATER", "R_EARLIER"], (
        f"Click-only fallback not sorted chronologically: got {order}"
    )
