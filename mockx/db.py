"""Read-only access to study.db."""
from __future__ import annotations

import json
import sqlite3

CONDITIONS = ("neutral", "agreeable", "satirical", "control")


def connect(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_post(conn, post_id):
    row = conn.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
    if row is None:
        return None
    post = dict(row)
    # Expose attached media as a parsed list; drop the raw JSON string.
    post["media"] = json.loads(post.pop("media_json", None) or "[]")
    return post


def get_intervention(conn, post_id, condition):
    row = conn.execute(
        "SELECT * FROM interventions WHERE post_id = ? AND condition = ?",
        (post_id, condition)).fetchone()
    return dict(row) if row else None


def get_thread(conn, post_id, condition):
    if condition not in CONDITIONS:
        return None
    post = get_post(conn, post_id)
    if post is None:
        return None
    intervention = get_intervention(conn, post_id, condition)
    if intervention is None:
        return None
    return {"post": post, "intervention": intervention}
