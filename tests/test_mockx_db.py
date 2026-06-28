"""Tests for mockx.db read helpers."""
from __future__ import annotations

import sqlite3

import pytest

from mockx import db as dbmod


def test_conditions_constant():
    assert dbmod.CONDITIONS == ("neutral", "agreeable", "satirical", "control")


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
