"""Tests for agent.cli.list_participants and agent.cli.bulk_register."""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.app.participants import InMemoryParticipantsStore, Participant, reset_store
from agent.cli.list_participants import main as list_main
from agent.cli.bulk_register import main as bulk_main


def _p(author_id, username, tone, day_offset=0):
    enrolled = datetime.now(timezone.utc) - timedelta(days=day_offset)
    return Participant(author_id=author_id, author_username=username,
                       tone=tone, enrolled_at_utc=enrolled)


@pytest.fixture(autouse=True)
def fresh_store():
    store = InMemoryParticipantsStore()
    reset_store(store)
    yield store
    reset_store(None)


# ── list_participants ─────────────────────────────────────────────────────────


class TestListParticipants:
    def test_empty_store(self, fresh_store, capsys):
        sys.argv = ["derad-list-participants"]
        list_main()
        assert "No participants" in capsys.readouterr().out

    def test_shows_all_participants(self, fresh_store, capsys):
        fresh_store.register(_p("1", "alice", "neutral", day_offset=5))
        fresh_store.register(_p("2", "bob", "agreeable", day_offset=2))
        sys.argv = ["derad-list-participants"]
        list_main()
        out = capsys.readouterr().out
        assert "@alice" in out
        assert "@bob" in out
        assert "neutral" in out
        assert "agreeable" in out
        assert "2 participant" in out

    def test_filter_by_tone(self, fresh_store, capsys):
        fresh_store.register(_p("1", "alice", "neutral"))
        fresh_store.register(_p("2", "bob", "agreeable"))
        sys.argv = ["derad-list-participants", "--tone", "neutral"]
        list_main()
        out = capsys.readouterr().out
        assert "@alice" in out
        assert "@bob" not in out

    def test_csv_format(self, fresh_store, capsys):
        fresh_store.register(_p("42", "charlie", "agonistic", day_offset=3))
        sys.argv = ["derad-list-participants", "--format", "csv"]
        list_main()
        out = capsys.readouterr().out
        reader = csv.DictReader(StringIO(out))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["author_id"] == "42"
        assert rows[0]["username"] == "charlie"
        assert rows[0]["tone"] == "agonistic"

    def test_study_day_calculation(self, fresh_store, capsys):
        # enrolled 4 days ago → study_day = 5
        fresh_store.register(_p("99", "daytest", "neutral", day_offset=4))
        sys.argv = ["derad-list-participants", "--format", "csv"]
        list_main()
        out = capsys.readouterr().out
        reader = csv.DictReader(StringIO(out))
        rows = list(reader)
        assert rows[0]["study_day"] == "5"


# ── bulk_register ─────────────────────────────────────────────────────────────


def _fake_client(user_id: str = "lookup_id"):
    client = MagicMock()
    resp = MagicMock()
    resp.data = {"id": user_id, "username": "ignored"}
    client.users.get_by_username.return_value = resp
    return client


class TestBulkRegister:
    def test_registers_from_csv(self, fresh_store, tmp_path, monkeypatch):
        csv_file = tmp_path / "participants.csv"
        csv_file.write_text("username,tone,enrolled\nalice,neutral,2026-05-20\n")

        # Return different IDs per username
        call_count = 0

        def _client():
            nonlocal call_count
            client = MagicMock()
            resp = MagicMock()
            resp.data = {"id": f"id_{call_count}", "username": "x"}
            call_count += 1
            client.users.get_by_username.return_value = resp
            return client

        monkeypatch.setattr("agent.cli.bulk_register.get_x_client", _client)
        sys.argv = ["derad-bulk-register", str(csv_file)]
        bulk_main()
        assert len(fresh_store.list_all()) == 1
        assert fresh_store.list_all()[0].author_username == "alice"

    def test_dry_run_does_not_write(self, fresh_store, tmp_path, monkeypatch):
        csv_file = tmp_path / "p.csv"
        csv_file.write_text("username,tone,enrolled\nbob,agreeable,2026-05-20\n")
        monkeypatch.setattr("agent.cli.bulk_register.get_x_client", lambda: _fake_client("555"))
        sys.argv = ["derad-bulk-register", str(csv_file), "--dry-run"]
        bulk_main()
        assert len(fresh_store.list_all()) == 0

    def test_random_tone_assigns_balanced(self, fresh_store, tmp_path, monkeypatch):
        # Pre-register 2 agreeable, 2 neutral → random should give agonistic
        for i in range(2):
            fresh_store.register(_p(f"a{i}", f"ua{i}", "agreeable"))
        for i in range(2):
            fresh_store.register(_p(f"n{i}", f"un{i}", "neutral"))

        csv_file = tmp_path / "p.csv"
        csv_file.write_text("username,tone,enrolled\nnewuser,random,2026-05-20\n")

        call_count = [0]

        def _client():
            client = MagicMock()
            resp = MagicMock()
            resp.data = {"id": f"new_{call_count[0]}", "username": "x"}
            call_count[0] += 1
            client.users.get_by_username.return_value = resp
            return client

        monkeypatch.setattr("agent.cli.bulk_register.get_x_client", _client)
        sys.argv = ["derad-bulk-register", str(csv_file)]
        bulk_main()

        new_participants = [p for p in fresh_store.list_all() if p.author_username == "newuser"]
        assert len(new_participants) == 1
        assert new_participants[0].tone == "agonistic"

    def test_api_error_skips_row(self, fresh_store, tmp_path, monkeypatch, capsys):
        csv_file = tmp_path / "p.csv"
        csv_file.write_text("username,tone,enrolled\nbaduser,neutral,2026-05-20\n")

        def _failing_client():
            client = MagicMock()
            client.users.get_by_username.side_effect = RuntimeError("API down")
            return client

        monkeypatch.setattr("agent.cli.bulk_register.get_x_client", _failing_client)
        sys.argv = ["derad-bulk-register", str(csv_file)]
        bulk_main()
        assert len(fresh_store.list_all()) == 0
        out = capsys.readouterr().out
        assert "skipped" in out
