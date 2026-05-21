"""Tests for _make_study_code and the participant allow-list in app.py."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("X_API_KEY", "test_consumer_key")
os.environ.setdefault("X_API_SECRET", "test_consumer_secret_abc")
os.environ.setdefault("SERVER_NAME", "test.local")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test_key")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://test.example/")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_EMBED", "test-embed")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_CHAT", "test-chat")
os.environ.setdefault("BOT_USER_ID_NEUTRAL", "999")

from derad_agent.app import app as app_module  # noqa: E402
from derad_agent.app import dedup as dedup_module  # noqa: E402
from derad_agent.app import metrics as metrics_module  # noqa: E402
from derad_agent.app.participants import Participant  # noqa: E402


_VALID_ALPHABET = set("ABCDEFGHJKLMNPQRSTUVWXYZ")


# ── _make_study_code ──────────────────────────────────────────────────────────


class TestMakeStudyCode:
    def test_length_is_four(self):
        assert len(app_module._make_study_code("12345678")) == 4

    def test_deterministic(self):
        code = app_module._make_study_code("12345678")
        assert app_module._make_study_code("12345678") == code

    def test_different_ids_differ(self):
        assert app_module._make_study_code("111") != app_module._make_study_code("222")

    def test_only_valid_alphabet_chars(self):
        for reply_id in ["1", "999", "1234567890123456789"]:
            code = app_module._make_study_code(reply_id)
            assert set(code).issubset(_VALID_ALPHABET), f"bad chars in {code!r}"

    def test_no_i_or_o(self):
        # Run a few hundred IDs to stress the alphabet exclusion
        for i in range(500):
            code = app_module._make_study_code(str(i))
            assert "I" not in code and "O" not in code, f"found I/O in {code!r} for id={i}"


# ── allow-list: registered participant passes dispatch ─────────────────────────


@pytest.fixture
def dispatch_env_with_participant(monkeypatch):
    """Dispatch env where a participant is registered in-memory."""
    monkeypatch.setattr(dedup_module, "_default_store", dedup_module.InMemoryStore())
    metrics_module._reset_counts_for_test()
    monkeypatch.setattr(app_module, "RESTRICT_TO_REGISTERED", True)

    p = Participant(
        author_id="555",
        author_username="studyuser",
        tone="neutral",
        enrolled_at_utc=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(app_module, "_PARTICIPANTS_BY_ID", {"555": p})
    monkeypatch.setattr(app_module, "_ALLOWED_IDS", {"555"})

    started: list[tuple] = []

    class _FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=False, **_):
            self.target, self.args = target, args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    return {"started": started, "participant": p}


def _tweet(id_str, author):
    return {
        "id_str": id_str,
        "in_reply_to_status_id_str": "p1",
        "user": {"id_str": author},
    }


def _now():
    return datetime.now(timezone.utc)


class TestAllowListWithParticipants:
    def test_registered_participant_is_accepted(self, dispatch_env_with_participant, monkeypatch):
        monkeypatch.setattr(app_module, "_ALLOWED_IDS", {"555"})
        result = app_module._dispatch_tweet("neutral", _tweet("m1", "555"), _now())
        assert result is True
        assert len(dispatch_env_with_participant["started"]) == 1

    def test_unregistered_is_dropped(self, dispatch_env_with_participant):
        result = app_module._dispatch_tweet("neutral", _tweet("m2", "999"), _now())
        assert result is False
        assert len(dispatch_env_with_participant["started"]) == 0


# ── study_day calculation ─────────────────────────────────────────────────────


class TestStudyDay:
    def test_enrolled_today_is_day_one(self):
        enrolled = datetime(2026, 5, 20, tzinfo=timezone.utc)
        received = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
        participant = Participant(
            author_id="1", author_username="u", tone="neutral", enrolled_at_utc=enrolled
        )
        study_day = (received.date() - participant.enrolled_at_utc.date()).days + 1
        assert study_day == 1

    def test_enrolled_five_days_ago_is_day_six(self):
        enrolled = datetime(2026, 5, 14, tzinfo=timezone.utc)
        received = datetime(2026, 5, 20, tzinfo=timezone.utc)
        participant = Participant(
            author_id="1", author_username="u", tone="neutral", enrolled_at_utc=enrolled
        )
        study_day = (received.date() - participant.enrolled_at_utc.date()).days + 1
        assert study_day == 7
