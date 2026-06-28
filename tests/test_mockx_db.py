"""Tests for mockx.db read helpers."""
from __future__ import annotations

import sqlite3

import pytest

from mockx import db as dbmod


def test_conditions_constant():
    assert dbmod.CONDITIONS == ("neutral", "agreeable", "satirical", "control")


def test_get_post_exposes_media_list(tmp_path):
    from mockx.build_db import build
    from tests.conftest import MOCKX_NOTES_CSV, MOCKX_SELECTED_CSV
    sel = tmp_path / "sel.csv"; sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes.csv"; notes.write_text(MOCKX_NOTES_CSV)
    media = tmp_path / "media.csv"
    media.write_text("tweetId,ordinal,type,path\nt1,0,photo,media/t1/0.jpg\n")
    out = tmp_path / "study.db"
    build(str(sel), str(notes), str(out), media_csv=str(media))

    conn = dbmod.connect(str(out))
    post = dbmod.get_post(conn, "t1")
    assert post["media"] == [{"type": "photo", "src": "/static/media/t1/0.jpg"}]
    assert "media_json" not in post                 # raw JSON string dropped
    assert dbmod.get_post(conn, "t2")["media"] == []  # no media -> empty list


def test_get_thread_bot_condition(mockx_db):
    conn = dbmod.connect(mockx_db)
    t = dbmod.get_thread(conn, "t1", "agreeable")
    assert t["post"]["post_id"] == "t1"
    assert t["intervention"]["kind"] == "bot_reply"
    assert t["intervention"]["bot_handle"] == "eddiexbot"


def test_get_thread_control_condition(mockx_db):
    conn = dbmod.connect(mockx_db)
    t = dbmod.get_thread(conn, "t1", "control")
    assert t["intervention"]["kind"] == "community_note"
    assert t["intervention"]["body"] == "Note for t1"


def test_get_thread_invalid_condition_is_none(mockx_db):
    conn = dbmod.connect(mockx_db)
    assert dbmod.get_thread(conn, "t1", "bogus") is None


def test_get_thread_missing_post_is_none(mockx_db):
    conn = dbmod.connect(mockx_db)
    assert dbmod.get_thread(conn, "nope", "neutral") is None


def test_connection_is_readonly(mockx_db):
    conn = dbmod.connect(mockx_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO posts (post_id) VALUES ('x')")
