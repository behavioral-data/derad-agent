"""Tests for mockx.build_db — synthesis helpers + study.db build."""
from __future__ import annotations

import sqlite3

from mockx.build_db import build, synth_author, synth_engagement
from tests.conftest import MOCKX_NOTES_CSV, MOCKX_SELECTED_CSV


def test_synth_author_is_deterministic_and_complete():
    a = synth_author("12345")
    assert a == synth_author("12345")
    assert a["name"] and a["handle"] and a["avatar"]
    assert a["verified"] in (0, 1)


def test_synth_author_varies_by_post():
    assert synth_author("111") != synth_author("222")


def test_synth_engagement_deterministic_and_bounded():
    e = synth_engagement("12345", "post")
    assert e == synth_engagement("12345", "post")
    assert 5000 <= e["views"] <= 200000
    assert e["likes"] >= 0 and e["reposts"] >= 0


def test_build_creates_170_style_rows(tmp_path):
    sel = tmp_path / "sel.csv"; sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes.csv"; notes.write_text(MOCKX_NOTES_CSV)
    db = tmp_path / "study.db"

    n_posts, n_iv = build(str(sel), str(notes), str(db))
    assert (n_posts, n_iv) == (2, 8)            # 2 posts × 4 conditions

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # dup tweetId collapsed to one post; both topics merged
    assert conn.execute("select count(*) from posts").fetchone()[0] == 2
    topics = conn.execute("select topic_condition from posts where post_id='t1'").fetchone()[0]
    assert set(topics.split(",")) == {"lgbt", "race"}

    conds = {r["condition"] for r in
             conn.execute("select condition from interventions where post_id='t1'")}
    assert conds == {"neutral", "agreeable", "satirical", "control"}

    bot = conn.execute(
        "select * from interventions where post_id='t1' and condition='neutral'").fetchone()
    assert bot["kind"] == "bot_reply"
    assert bot["bot_handle"] == "eddiexbot"
    assert bot["is_stub"] == 1

    note = conn.execute(
        "select * from interventions where post_id='t1' and condition='control'").fetchone()
    assert note["kind"] == "community_note"
    assert note["body"] == "Note for t1"
    assert note["source_note_id"] == "n1"
    assert note["is_stub"] == 0


def test_bot_reply_engagement_identical_across_tones(tmp_path):
    sel = tmp_path / "sel.csv"; sel.write_text(MOCKX_SELECTED_CSV)
    notes = tmp_path / "notes.csv"; notes.write_text(MOCKX_NOTES_CSV)
    db = tmp_path / "study.db"
    build(str(sel), str(notes), str(db))
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select reply_likes, reply_views from interventions "
        "where post_id='t1' and kind='bot_reply'").fetchall()
    assert len({(r["reply_likes"], r["reply_views"]) for r in rows}) == 1
