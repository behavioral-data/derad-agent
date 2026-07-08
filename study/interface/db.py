"""Access helpers for study.db (read-only and batch reply updates)."""
from __future__ import annotations

import json
import logging
import sqlite3

CONDITIONS = ("neutral", "agreeable", "satirical", "control")
BOT_TONES = ("neutral", "agreeable", "satirical")

log = logging.getLogger(__name__)


def connect(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_writable(db_path):
    conn = sqlite3.connect(db_path)
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


def list_posts(conn):
    """All posts summarized for the browse/demo page: id, author, snippet,
    study conditions, and a compact media badge (img/vid/gif + count)."""
    rows = conn.execute(
        "SELECT post_id, author_name, author_handle, content, "
        "topic_condition, polarity_condition, media_json FROM posts "
        "ORDER BY topic_condition, polarity_condition, post_id"
    ).fetchall()
    out = []
    for r in rows:
        media = json.loads(r["media_json"] or "[]")
        badge = ""
        if media:
            t = media[0]["type"]
            kind = "vid" if t == "video" else "gif" if t == "animated_gif" else "img"
            badge = f"{kind}·{len(media)}"
        out.append({
            "post_id": r["post_id"],
            "author_name": r["author_name"],
            "author_handle": r["author_handle"],
            "content": r["content"],
            "topic": r["topic_condition"],
            "polarity": r["polarity_condition"],
            "media": badge,
        })
    return out


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


def update_bot_replies(conn, post_id, replies):
    """Write generated bot replies into interventions; return rows updated."""
    if conn.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone() is None:
        raise LookupError(f"post_id {post_id!r} not found in study.db posts table")

    updated = 0
    for tone in BOT_TONES:
        body = (replies.get(tone) or "").strip()
        if not body:
            log.warning("id=%s tone=%s — empty reply, skipping db update", post_id, tone)
            continue
        cur = conn.execute(
            """
            UPDATE interventions
            SET body = ?, is_stub = 0
            WHERE post_id = ? AND condition = ? AND kind = 'bot_reply'
            """,
            (body, post_id, tone),
        )
        if cur.rowcount == 0:
            log.warning(
                "id=%s tone=%s — no bot_reply intervention row to update", post_id, tone,
            )
            continue
        updated += cur.rowcount
    return updated
