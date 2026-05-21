"""Tests for derad_agent.cli.register_participant."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone

import pytest

from derad_agent.app.participants import InMemoryParticipantsStore, reset_store
from derad_agent.cli.register_participant import main


@pytest.fixture(autouse=True)
def fresh_store():
    store = InMemoryParticipantsStore()
    reset_store(store)
    yield store
    reset_store(None)


def _run(args: list[str]):
    sys.argv = ["derad-register-participant"] + args
    main()


class TestRegisterParticipantCLI:
    def test_registers_participant(self, fresh_store):
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
            "--tone", "satirical",
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
