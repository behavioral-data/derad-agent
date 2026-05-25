"""Tests for agent.cli.register_participant."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from agent.app.participants import InMemoryParticipantsStore, Participant, reset_store
from agent.cli.register_participant import _pick_tone, main


@pytest.fixture(autouse=True)
def fresh_store():
    store = InMemoryParticipantsStore()
    reset_store(store)
    yield store
    reset_store(None)


def _run(args: list[str]):
    sys.argv = ["derad-register-participant"] + args
    main()


def _fake_user_response(user_id: str = "99999"):
    resp = MagicMock()
    resp.data = {"id": user_id, "username": "testuser"}
    return resp


class TestRegisterParticipantCLI:
    def test_registers_with_explicit_author_id(self, fresh_store):
        _run(["--author-id", "12345", "--username", "janesmith", "--tone", "neutral"])
        p = fresh_store.get("12345")
        assert p is not None
        assert p.author_username == "janesmith"
        assert p.tone == "neutral"

    def test_strips_at_prefix_from_username(self, fresh_store):
        _run(["--author-id", "99", "--username", "@handle", "--tone", "agreeable"])
        assert fresh_store.get("99").author_username == "handle"

    def test_explicit_enrolled_date(self, fresh_store):
        _run([
            "--author-id", "77",
            "--username", "bob",
            "--tone", "agonistic",
            "--enrolled", "2026-04-01",
        ])
        assert fresh_store.get("77").enrolled_at_utc.date() == date(2026, 4, 1)

    def test_default_enrolled_is_today_midnight_utc(self, fresh_store):
        _run(["--author-id", "88", "--username", "tod", "--tone", "neutral"])
        enrolled = fresh_store.get("88").enrolled_at_utc
        today = datetime.now(timezone.utc).date()
        assert enrolled.date() == today
        assert enrolled.hour == 0 and enrolled.minute == 0

    def test_invalid_date_exits(self, fresh_store):
        with pytest.raises(SystemExit) as exc_info:
            _run([
                "--author-id", "1",
                "--username", "x",
                "--tone", "neutral",
                "--enrolled", "not-a-date",
            ])
        assert exc_info.value.code == 1

    def test_lookup_by_username_when_no_author_id(self, fresh_store, monkeypatch):
        fake_client = MagicMock()
        fake_client.users.get_by_username.return_value = _fake_user_response("77777")
        monkeypatch.setattr(
            "agent.cli.register_participant.get_x_client",
            lambda: fake_client,
        )
        _run(["--username", "lookedupuser", "--tone", "neutral"])
        p = fresh_store.get("77777")
        assert p is not None
        assert p.author_username == "lookedupuser"
        fake_client.users.get_by_username.assert_called_once_with(username="lookedupuser")


class TestPickTone:
    def test_explicit_tone_returned_unchanged(self, fresh_store):
        assert _pick_tone("agreeable") == "agreeable"

    def test_random_picks_least_used(self, fresh_store):
        # Register 2 agreeable, 2 neutral, 0 agonistic → random should pick agonistic
        for i in range(2):
            fresh_store.register(Participant(
                author_id=f"a{i}", author_username=f"u{i}",
                tone="agreeable", enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
            ))
        for i in range(2):
            fresh_store.register(Participant(
                author_id=f"n{i}", author_username=f"v{i}",
                tone="neutral", enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
            ))
        assert _pick_tone("random") == "agonistic"

    def test_random_with_empty_store_picks_any_valid_tone(self, fresh_store):
        assert _pick_tone("random") in {"agreeable", "neutral", "agonistic"}
