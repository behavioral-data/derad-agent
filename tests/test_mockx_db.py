"""Tests for mockx.db read helpers."""
from __future__ import annotations

import sqlite3

import pytest

from study.interface import db as dbmod


def test_conditions_constant():
    assert dbmod.CONDITIONS == ("neutral", "agreeable", "satirical", "control")


def test_get_post_exposes_media_list(tmp_path):
    from study.interface.build_db import build
    from tests.conftest import MOCKX_NOTES_CSV, MOCKX_SELECTED_CSV
    sel = tmp_path / "sel.csv"; sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes.csv"; notes.write_text(MOCKX_NOTES_CSV)
    media = tmp_path / "media.csv"
    media.write_text("tweetId,ordinal,type,path\nt1,0,photo,t1/0.jpg\n")
    out = tmp_path / "study.db"
    build(str(sel), str(notes), str(out), media_csv=str(media))

    conn = dbmod.connect(str(out))
    post = dbmod.get_post(conn, "t1")
    assert post["media"] == [{"type": "photo", "src": "/media/t1/0.jpg"}]
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


def test_list_posts_includes_opaque_codes(mockx_db):
    conn = dbmod.connect(mockx_db)
    posts = dbmod.list_posts(conn)
    by_id = {p["post_id"]: p for p in posts}
    assert set(by_id) == {"t1", "t2"}
    t1 = by_id["t1"]
    assert set(t1) >= {"post_id", "author_name", "author_handle", "content",
                       "topic", "polarity", "media", "codes"}
    assert set(t1["codes"]) == set(dbmod.CONDITIONS)          # a code per condition
    assert len(set(t1["codes"].values())) == len(dbmod.CONDITIONS)  # all distinct


def test_resolve_code_roundtrip(mockx_db):
    conn = dbmod.connect(mockx_db)
    p = dbmod.list_posts(conn)[0]
    for cond, code in p["codes"].items():
        assert dbmod.resolve_code(conn, code) == (p["post_id"], cond)
        assert dbmod.get_thread_by_code(conn, code)["post"]["post_id"] == p["post_id"]
    assert dbmod.resolve_code(conn, "deadbeef0000") is None
    assert dbmod.get_thread_by_code(conn, "deadbeef0000") is None


def test_browse_links_use_opaque_codes_only(mockx_db):
    from study.interface.server import create_app
    client = create_app(db_path=mockx_db).test_client()
    r = client.get("/browse")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert body.count("/?v=") >= 8         # 2 posts x 4 conditions, code links only
    assert "condition=" not in body        # no readable condition/post leaks in URLs
    assert "post_id=" not in body


def test_api_thread_by_code_hides_condition(mockx_db):
    from study.interface.server import create_app
    conn = dbmod.connect(mockx_db)
    code = dbmod.list_posts(conn)[0]["codes"]["satirical"]
    client = create_app(db_path=mockx_db).test_client()
    r = client.get(f"/api/thread?v={code}")
    assert r.status_code == 200
    assert "condition" not in r.get_json()["intervention"]   # stripped from response
    assert client.get("/api/thread?v=deadbeef0000").status_code == 404
