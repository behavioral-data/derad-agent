"""Tests for study.db bot-reply updates."""
from __future__ import annotations

import sqlite3

import pytest

from mockx.build_db import build
from mockx.db import update_bot_replies
from tests.conftest import MOCKX_NOTES_CSV, MOCKX_SELECTED_CSV


@pytest.fixture
def tiny_study_db(tmp_path):
    sel = tmp_path / "sel.csv"
    sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes.csv"
    notes.write_text(MOCKX_NOTES_CSV)
    db = tmp_path / "study.db"
    build(str(sel), str(notes), str(db))
    return db


def test_update_bot_replies_replaces_stub_bodies(tiny_study_db):
    replies = {
        "neutral": "Neutral reply text",
        "satirical": "Satirical reply text",
        "agreeable": "Agreeable reply text",
    }
    conn = sqlite3.connect(str(tiny_study_db))
    conn.row_factory = sqlite3.Row
    try:
        assert update_bot_replies(conn, "t1", replies) == 3
        conn.commit()
        for tone, body in replies.items():
            row = conn.execute(
                "SELECT body, is_stub FROM interventions "
                "WHERE post_id = 't1' AND condition = ? AND kind = 'bot_reply'",
                (tone,),
            ).fetchone()
            assert row["body"] == body
            assert row["is_stub"] == 0
        control = conn.execute(
            "SELECT body FROM interventions WHERE post_id = 't1' AND condition = 'control'"
        ).fetchone()
        assert control["body"] == "Note for t1"
    finally:
        conn.close()


def test_update_bot_replies_unknown_post_raises(tiny_study_db):
    conn = sqlite3.connect(str(tiny_study_db))
    try:
        with pytest.raises(LookupError, match="not found"):
            update_bot_replies(conn, "nope", {"neutral": "x", "satirical": "x", "agreeable": "x"})
    finally:
        conn.close()
