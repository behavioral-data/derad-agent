"""Tests for agent.cli.daily_summary."""

from __future__ import annotations

import os
from datetime import date

from agent.cli.daily_summary import _BOT_HANDLE, _get_events_for_date, main


class TestBotHandle:
    def test_uses_env_handle(self):
        assert _BOT_HANDLE == os.getenv("BOT_HANDLE", "eddiexbot")


class TestGetEventsForDate:
    def test_memory_backend_returns_empty(self, monkeypatch):
        monkeypatch.setenv("DERAD_EVENTS_BACKEND", "memory")
        result = _get_events_for_date(date(2026, 5, 19))
        assert result == []

    def test_default_backend_returns_empty(self, monkeypatch):
        monkeypatch.delenv("DERAD_EVENTS_BACKEND", raising=False)
        result = _get_events_for_date(date(2026, 5, 19))
        assert result == []


class TestDailySummaryMain:
    def _event(self, participant_id="p1", study_code="ABCD", tone="neutral", study_day=3):
        return {
            "mention_id": "m1",
            "reply_id": "r1",
            "study_code": study_code,
            "participant_id": participant_id,
            "author_username": "testuser",
            "tone": tone,
            "study_day": study_day,
            "parent_id": "pp1",
            "outcome": "replied",
        }

    def test_no_events_prints_message(self, monkeypatch, capsys):
        monkeypatch.setattr("agent.cli.daily_summary._get_events_for_date", lambda d: [])
        import sys
        sys.argv = ["derad-daily-summary", "--date", "2026-05-19"]
        main()
        out = capsys.readouterr().out
        assert "No study replies" in out

    def test_groups_by_participant(self, monkeypatch, capsys):
        events = [
            self._event("p1", "ABCD"),
            self._event("p1", "WXYZ"),
            self._event("p2", "MNPQ"),
        ]
        monkeypatch.setattr("agent.cli.daily_summary._get_events_for_date", lambda d: events)
        import sys
        sys.argv = ["derad-daily-summary", "--date", "2026-05-19"]
        main()
        out = capsys.readouterr().out
        assert "p1" in out
        assert "p2" in out
        assert "ABCD" in out
        assert "WXYZ" in out
        assert "MNPQ" in out
        assert "Total: 3 replies across 2 participant(s)" in out

    def test_url_format(self, monkeypatch, capsys):
        events = [self._event("p1", "ABCD", tone="neutral")]
        monkeypatch.setattr("agent.cli.daily_summary._get_events_for_date", lambda d: events)
        import sys
        sys.argv = ["derad-daily-summary", "--date", "2026-05-19"]
        main()
        out = capsys.readouterr().out
        assert f"https://x.com/{_BOT_HANDLE}/status/r1" in out
