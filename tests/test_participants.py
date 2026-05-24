"""Tests for derad_agent.app.participants."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from derad_agent.app.participants import (
    InMemoryParticipantsStore,
    Participant,
    _entity_to_participant,
    get_store,
    reset_store,
)


def _participant(author_id="100", tone="neutral") -> Participant:
    return Participant(
        author_id=author_id,
        author_username="testuser",
        tone=tone,
        enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


# ── InMemoryParticipantsStore ─────────────────────────────────────────────────


class TestInMemoryStore:
    def setup_method(self):
        reset_store(InMemoryParticipantsStore())

    def teardown_method(self):
        reset_store(None)

    def test_register_and_get(self):
        store = get_store()
        p = _participant("42")
        store.register(p)
        assert store.get("42") is p

    def test_get_missing_returns_none(self):
        assert get_store().get("nonexistent") is None

    def test_list_all_returns_all(self):
        store = get_store()
        p1 = _participant("1")
        p2 = _participant("2")
        store.register(p1)
        store.register(p2)
        ids = {p.author_id for p in store.list_all()}
        assert ids == {"1", "2"}

    def test_register_upserts(self):
        store = get_store()
        store.register(_participant("7"))
        updated = Participant(
            author_id="7",
            author_username="newhandle",
            tone="agreeable",
            enrolled_at_utc=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        store.register(updated)
        assert store.get("7").author_username == "newhandle"
        assert len(store.list_all()) == 1


# ── _entity_to_participant ────────────────────────────────────────────────────


class TestEntityToParticipant:
    def _base(self, **overrides):
        ent = {
            "RowKey": "99",
            "author_username": "alice",
            "tone": "agreeable",
            "enrolled_at_utc": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "notes": "",
        }
        ent.update(overrides)
        return ent

    def test_aware_datetime_passthrough(self):
        p = _entity_to_participant(self._base())
        assert p.enrolled_at_utc.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        p = _entity_to_participant(self._base(enrolled_at_utc=datetime(2026, 5, 1)))
        assert p.enrolled_at_utc.tzinfo == timezone.utc

    def test_string_date_parsed(self):
        p = _entity_to_participant(self._base(enrolled_at_utc="2026-05-01T00:00:00"))
        assert p.enrolled_at_utc.year == 2026
        assert p.enrolled_at_utc.tzinfo == timezone.utc

    def test_missing_optional_fields_default(self):
        ent = {"RowKey": "50", "enrolled_at_utc": datetime(2026, 5, 1, tzinfo=timezone.utc)}
        p = _entity_to_participant(ent)
        assert p.author_username == ""
        assert p.tone == ""
        assert p.notes == ""
