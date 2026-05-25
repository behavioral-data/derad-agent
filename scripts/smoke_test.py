#!/usr/bin/env python
"""End-to-end smoke test for the derad-agent study workflow.

Exercises every phase in one process with no external API calls:
  1. Participant registration (metadata only — bot replies to everyone)
  2. Open dispatch (unregistered user is accepted, no allow-list gate)
  3. Mention → process_mention → study_code + study_day stamped
  4. MentionEvent written to store
  5. Poll engagement (3-day-old reply, mocked X metrics)
  6. Collect replies (3-day-old reply, mocked X search)
  7. Daily summary output

Usage:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import os
import sys

# ── Env vars BEFORE any app imports ──────────────────────────────────────────
os.environ["DERAD_EVENTS_BACKEND"] = "memory"
os.environ["DERAD_PARTICIPANTS_BACKEND"] = "memory"
os.environ["DERAD_INGEST_MODE"] = "off"          # prevent streamer from starting
os.environ["DERAD_DRY_RUN"] = "false"            # override .env so we test the full path
os.environ.setdefault("SERVER_NAME", "localhost:5000")
os.environ.setdefault("X_API_KEY", "smoke-fake-key")
os.environ.setdefault("X_API_SECRET", "smoke-fake-secret")
os.environ.setdefault("BOT_HANDLE", "eddiexbot")
os.environ.setdefault("BOT_USER_ID", "bot_eddie_999")
os.environ.setdefault("X_ACCESS_TOKEN", "smoke-fake-access-token")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "smoke-fake-access-secret")

# ── Imports (order matters) ───────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from agent.app.participants import (
    InMemoryParticipantsStore,
    Participant,
    reset_store as reset_participants_store,
)

# ── ANSI helpers ──────────────────────────────────────────────────────────────
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

_failures: list[str] = []


def ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}✗ FAIL:{_RESET} {msg}")
    _failures.append(msg)


def section(title: str) -> None:
    print(f"\n{_BOLD}{_CYAN}━━ {title} ━━{_RESET}")


def assert_eq(label: str, actual, expected) -> None:
    if actual == expected:
        ok(f"{label} = {actual!r}")
    else:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(label: str, value) -> None:
    if value:
        ok(label)
    else:
        fail(label)


# ═════════════════════════════════════════════════════════════════════════════
# Phase 1 — Participant registration
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 1 · Participant registration")

p_store = InMemoryParticipantsStore()
reset_participants_store(p_store)

ENROLLED = datetime(2026, 5, 15, tzinfo=timezone.utc)   # enrolled 5 days ago
RECEIVED = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)

participant = Participant(
    author_id="test_author_123",
    author_username="studyparticipant",
    tone="neutral",
    enrolled_at_utc=ENROLLED,
)
p_store.register(participant)

assert_true("participant registered", p_store.get("test_author_123") is not None)
assert_eq("username", p_store.get("test_author_123").author_username, "studyparticipant")
assert_eq("list_all length", len(p_store.list_all()), 1)

# ═════════════════════════════════════════════════════════════════════════════
# Phase 2 — Import app (participants metadata only, no allow-list gate)
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 2 · App startup (participant metadata loaded, no allow-list)")

# Import app NOW so _PARTICIPANTS_BY_ID is populated from our store.
from agent.app import app as app_module
from agent.app import dedup as dedup_module
from agent.app import events as events_module

# Reset dedup + events store.
dedup_module._default_store = dedup_module.InMemoryStore()
e_store = events_module.InMemoryEventsStore()
events_module.reset_store(e_store)

assert_true("participant in _PARTICIPANTS_BY_ID", "test_author_123" in app_module._PARTICIPANTS_BY_ID)
print(f"  {_CYAN}participants loaded (metadata only): {len(app_module._PARTICIPANTS_BY_ID)}{_RESET}")

# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 — Open dispatch: unregistered authors are accepted (no allow-list)
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 3 · Open dispatch (no allow-list gate)")


class _SyncThread:
    """Runs process_mention synchronously for testing."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
        self._target, self._args = target, args
    def start(self):
        self._target(*self._args)


# Unregistered author should now be accepted (no allow-list gate)
unregd_tweet = {
    "id_str": "m_unregistered",
    "in_reply_to_status_id_str": "parent_001",
    "user": {"id_str": "unknown_author_456"},
}
with patch.object(app_module.threading, "Thread", _SyncThread):
    with patch.object(app_module, "fetch_tweet", return_value=None):
        result = app_module._dispatch_tweet(unregd_tweet, RECEIVED)
assert_eq("unregistered author → accepted (True)", result, True)

# Unregistered users get a randomly-assigned tone; assert it landed in the event log.
last_drop_or_event_tone = (e_store.events[-1].tone if e_store.events else None) or \
    (e_store.drops[-1].tone if e_store.drops else None)
assert_true(
    "unregistered mention got a valid random tone",
    last_drop_or_event_tone in ("agreeable", "neutral", "satirical"),
)

drop_reasons = [d.drop_reason for d in e_store.drops]
assert_true("no 'unregistered' drop reason recorded", "unregistered" not in drop_reasons)

# Registered author: fresh dedup so previous dispatch doesn't block
dedup_module._default_store = dedup_module.InMemoryStore()

registered_tweet = {
    "id_str": "m_registered",
    "in_reply_to_status_id_str": "parent_002",
    "user": {"id_str": "test_author_123"},
}

with patch.object(app_module.threading, "Thread", _SyncThread):
    with patch.object(app_module, "fetch_tweet", return_value=None):
        # fetch_tweet returning None triggers parent_fetch_failed outcome
        result2 = app_module._dispatch_tweet(registered_tweet, RECEIVED)

assert_eq("registered author → accepted (True)", result2, True)
# Registered participant ("studyparticipant") was registered with tone="neutral".
reg_ev = e_store.events[-1]
assert_eq("registered author got their assigned tone", reg_ev.tone, "neutral")

# ═════════════════════════════════════════════════════════════════════════════
# Phase 4 — Mention processing: study fields
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 4 · Mention processing → study_code + study_day")

e_store2 = events_module.InMemoryEventsStore()
events_module.reset_store(e_store2)
dedup_module._default_store = dedup_module.InMemoryStore()

MENTION_TWEET = {
    "id_str": "mention_001",
    "in_reply_to_status_id_str": "parent_tweet_456",
    "user": {"id_str": "test_author_123", "screen_name": "studyparticipant"},
    "text": "@neutralbot The climate policy is working well.",
}

from agent.app.utils import TweetSnapshot

fake_snap = TweetSnapshot(
    text="The climate policy is working well.",
    author_id="parent_author_789",
    author_username="origposter",
    like_count=120,
    retweet_count=40,
    reply_count=5,
    quote_count=2,
)
fake_reply = {
    "text": "Community notes reveal the policy's mixed track record.",
    "sources": ["https://x.com/i/web/status/t1"],
    "tweets": ["t1"],
    "notes": ["n1"],
    "queries": ["climate policy effectiveness"],
    "all_cited_tweet_ids": ["t1"],
    "all_cited_note_ids": ["n1"],
}

# Force DRY_RUN=False in case .env had it set true — we need the full posting path.
_old_dry_run = app_module.DRY_RUN
app_module.DRY_RUN = False
try:
    with patch.object(app_module, "fetch_tweet", return_value=fake_snap):
        with patch.object(app_module, "generate_reply", return_value=fake_reply):
            with patch.object(app_module, "post_reply", return_value="reply_tweet_789"):
                app_module.process_mention("neutral", MENTION_TWEET, RECEIVED)
finally:
    app_module.DRY_RUN = _old_dry_run

assert_eq("events store has 1 event", len(e_store2.events), 1)

ev = e_store2.events[0]
assert_eq("mention_id", ev.mention_id, "mention_001")
assert_eq("outcome", ev.outcome, "replied")
assert_eq("reply_id", ev.reply_id, "reply_tweet_789")
assert_eq("participant_id", ev.participant_id, "test_author_123")

# study_code: 4 chars, only valid alphabet (no I or O)
sc = ev.study_code
assert_true("study_code is 4 chars", sc is not None and len(sc) == 4)
_VALID = set("ABCDEFGHJKLMNPQRSTUVWXYZ")
assert_true("study_code uses valid alphabet (no I/O)", sc is not None and set(sc).issubset(_VALID))

# study_day: enrolled May 15, received May 20 → day 6
assert_eq("study_day", ev.study_day, 6)

# Determinism check
code2 = app_module._make_study_code("reply_tweet_789")
assert_eq("study_code is deterministic", ev.study_code, code2)

print(f"  {_CYAN}study_code={ev.study_code!r}  study_day={ev.study_day}  reply_id={ev.reply_id!r}{_RESET}")

# ═════════════════════════════════════════════════════════════════════════════
# Phase 5 — Poll engagement (3-day window)
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 5 · Poll engagement on 3-day-old reply")

from agent.cli.poll_engagement import _poll_one

# Write a MentionEvent whose reply_posted_utc is exactly 3.5 days ago
now = datetime.now(timezone.utc)
old_posted = now - timedelta(hours=84)  # 3.5 days ago

old_ev = events_module.MentionEvent(
    mention_id="old_mention",
    parent_id="old_parent",
    author_id="test_author_123",
    tone="neutral",
    received_at_utc=old_posted,
    reply_posted_utc=old_posted,
    reply_id="old_reply_tweet",
)
e_store2.write_event(old_ev)

# Verify the event is old enough to be eligible for snapshotting
from agent.app.events import SNAPSHOT_MIN_AGE
assert_true("old reply is ≥3 days old", now - old_posted >= SNAPSHOT_MIN_AGE)

# Mock X client returning fake metrics
fake_metrics_response = MagicMock()
fake_metrics_response.data = {
    "public_metrics": {
        "like_count": 47,
        "retweet_count": 12,
        "reply_count": 8,
        "quote_count": 3,
    }
}
fake_x_client = MagicMock()
fake_x_client.posts.get_by_id.return_value = fake_metrics_response

with patch("agent.cli.poll_engagement.get_x_client", return_value=fake_x_client):
    _poll_one("old_reply_tweet", "neutral", mention_id="old_mention", parent_id="old_parent")

assert_eq("engagement snapshots written", len(e_store2.engagements), 1)
snap = e_store2.engagements[0]
assert_eq("like_count", snap.like_count, 47)
assert_eq("retweet_count", snap.retweet_count, 12)
assert_eq("reply_count", snap.reply_count, 8)
assert_eq("mention_id back-link", snap.mention_id, "old_mention")

print(f"  {_CYAN}likes={snap.like_count} retweets={snap.retweet_count} replies={snap.reply_count}{_RESET}")

# ═════════════════════════════════════════════════════════════════════════════
# Phase 6 — Collect replies (3-day window)
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 6 · Collect bystander replies on 3-day-old reply")

from agent.cli.collect_replies import _collect_one

bystander_tweet = {
    "id": "bystander_reply_001",
    "author_id": "bystander_user_999",
    "text": "This bot actually makes a good point.",
    "in_reply_to_tweet_id": "old_reply_tweet",
    "public_metrics": {"like_count": 5},
}
bystander_user = {"id": "bystander_user_999", "username": "bystanderhandle"}

fake_search_response = MagicMock()
fake_search_response.data = [bystander_tweet]
fake_search_response.includes = {"users": [bystander_user]}

fake_collect_client = MagicMock()
fake_collect_client.tweets.search_recent.return_value = fake_search_response

with patch("agent.cli.collect_replies.get_x_client", return_value=fake_collect_client):
    count = _collect_one(
        "old_reply_tweet", "neutral",
        mention_id="old_mention", parent_id="old_parent",
    )

assert_eq("bystander replies collected", count, 1)
assert_eq("BotReplyReply rows in store", len(e_store2.reply_replies), 1)

rr = e_store2.reply_replies[0]
assert_eq("reply_tweet_id", rr.reply_tweet_id, "bystander_reply_001")
assert_eq("author_username", rr.author_username, "bystanderhandle")
assert_eq("like_count", rr.like_count, 5)
assert_eq("mention_id back-link", rr.mention_id, "old_mention")
assert_eq("tone", rr.tone, "neutral")

print(f"  {_CYAN}bystander: @{rr.author_username} — {rr.text!r}{_RESET}")

# ═════════════════════════════════════════════════════════════════════════════
# Phase 7 — Daily summary output
# ═════════════════════════════════════════════════════════════════════════════
section("Phase 7 · Daily summary (researcher view)")

from agent.cli.daily_summary import main as daily_summary_main

synthetic_events = [
    {
        "mention_id": "mention_001",
        "reply_id": "reply_tweet_789",
        "study_code": ev.study_code,
        "participant_id": "test_author_123",
        "author_username": "studyparticipant",
        "tone": "neutral",
        "study_day": 6,
        "parent_id": "parent_tweet_456",
        "outcome": "replied",
    }
]

sys.argv = ["derad-daily-summary", "--date", "2026-05-20"]
with patch("agent.cli.daily_summary._get_events_for_date", return_value=synthetic_events):
    daily_summary_main()

ok("daily summary printed without error")

# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════
print()
if _failures:
    print(f"{_RED}{_BOLD}SMOKE TEST FAILED — {len(_failures)} failure(s):{_RESET}")
    for f in _failures:
        print(f"  {_RED}• {f}{_RESET}")
    sys.exit(1)
else:
    print(f"{_GREEN}{_BOLD}All phases passed.{_RESET}")
