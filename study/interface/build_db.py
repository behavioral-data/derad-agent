"""Build the read-only study.db from selected_posts.csv + notes_selected.csv.

Fast (stdlib only). Re-runnable: drops and recreates both tables.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_SELECTED = os.path.join(_ROOT, "tsv_generation", "selected_posts.csv")
DEFAULT_NOTES_CSV = os.path.join(_HERE, "data", "notes_selected.csv")
DEFAULT_MEDIA_CSV = os.path.join(_HERE, "data", "media_index.csv")
DEFAULT_REPLIES_CSV = os.path.join(_ROOT, "tsv_generation", "selected_posts_replies.csv")
DEFAULT_DB = os.path.join(_HERE, "study.db")

csv.field_size_limit(10_000_000)

BOT_NAME = "Eddie"
BOT_HANDLE = "eddiexbot"
BOT_AVATAR = "ED"
TONES = ("neutral", "agreeable", "satirical")

_FIRST = ["Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn",
          "Cameron", "Skyler", "Reese", "Devon", "Harper", "Rowan", "Emerson",
          "Finley", "Sawyer", "Drew", "Hayden", "Peyton", "Marlowe"]
_LAST = ["Bennett", "Carter", "Dawson", "Ellis", "Foster", "Grant", "Hayes",
         "Ingram", "Jensen", "Keller", "Lawson", "Mercer", "Nolan", "Owens",
         "Porter", "Reyes", "Sutton", "Tate", "Underwood", "Vance"]


def _h(s):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def synth_author(post_id):
    h = _h("author:" + str(post_id))
    first = _FIRST[h % len(_FIRST)]
    last = _LAST[(h // 97) % len(_LAST)]
    return {
        "name": f"{first} {last}",
        "handle": f"{first.lower()}{last.lower()}{h % 1000}",
        "avatar": (first[0] + last[0]).upper(),
        "verified": 1 if h % 10 == 0 else 0,
    }


def synth_engagement(post_id, salt):
    h = _h(f"engage:{salt}:{post_id}")
    views = 5000 + (h % 195001)                       # 5_000 .. 200_000
    likes = int(views * (0.01 + (h % 40) / 1000.0))   # 1% .. 5% of views
    reposts = int(likes * (0.10 + (h % 30) / 100.0))  # 10% .. 39% of likes
    return {"likes": likes, "reposts": reposts, "views": views}


def load_posts(selected_csv):
    """Dedupe by tweetId; merge topic_condition across duplicate rows."""
    order, rows = [], {}
    with open(selected_csv, newline="") as f:
        for r in csv.DictReader(f):
            tid = r["tweetId"]
            if tid not in rows:
                rows[tid] = {
                    "content": r["text"],
                    "created_at": r["created_at"],
                    "polarity_condition": r.get("polarity_condition", ""),
                    "topics": set(),
                }
                order.append(tid)
            if r.get("topic_condition"):
                rows[tid]["topics"].add(r["topic_condition"])
    posts = []
    for tid in order:
        r = rows[tid]
        a = synth_author(tid)
        e = synth_engagement(tid, "post")
        posts.append({
            "post_id": tid,
            "content": r["content"],
            "created_at": r["created_at"],
            "author_name": a["name"],
            "author_handle": a["handle"],
            "author_verified": a["verified"],
            "likes": e["likes"],
            "reposts": e["reposts"],
            "views": e["views"],
            "polarity_condition": r["polarity_condition"],
            "topic_condition": ",".join(sorted(r["topics"])),
        })
    return posts


def _load_notes(notes_csv):
    with open(notes_csv, newline="") as f:
        return {r["tweetId"]: r for r in csv.DictReader(f)}


def _load_media(media_csv):
    """tweetId -> ordered list of {"type", "src"} from media_index.csv.

    Returns {} when no media_csv is given or the file is absent (e.g. tests
    that don't exercise media). `src` is the runtime URL under /static/.
    """
    by_tweet = {}
    if not media_csv or not os.path.exists(media_csv):
        return by_tweet
    with open(media_csv, newline="") as f:
        for r in csv.DictReader(f):
            by_tweet.setdefault(r["tweetId"], []).append(
                (int(r["ordinal"]), {"type": r["type"], "src": "/static/" + r["path"]}))
    return {tid: [m for _, m in sorted(items)] for tid, items in by_tweet.items()}


def _load_replies(replies_csv):
    """post_id -> {tone: reply_body} from selected_posts_replies.csv (cols: id,<tones>).

    Returns {} when no file is given/present (e.g. tests, or before generation),
    in which case bot replies fall back to stub text.
    """
    by_post = {}
    if not replies_csv or not os.path.exists(replies_csv):
        return by_post
    with open(replies_csv, newline="") as f:
        for r in csv.DictReader(f):
            pid = (r.get("id") or r.get("tweetId") or "").strip()
            if pid:
                by_post[pid] = {t: (r.get(t) or "").strip() for t in TONES}
    return by_post


def build(selected_csv, notes_csv, out_db, media_csv=None, replies_csv=None):
    posts = load_posts(selected_csv)
    notes = _load_notes(notes_csv)
    media = _load_media(media_csv)
    replies = _load_replies(replies_csv)

    tmp_db = out_db + ".tmp"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.execute("""
        CREATE TABLE posts (
            post_id TEXT PRIMARY KEY, content TEXT, created_at TEXT,
            author_name TEXT, author_handle TEXT, author_verified INTEGER,
            likes INTEGER, reposts INTEGER, views INTEGER,
            polarity_condition TEXT, topic_condition TEXT, media_json TEXT)""")
    conn.execute("""
        CREATE TABLE interventions (
            post_id TEXT, condition TEXT, kind TEXT, body TEXT,
            bot_name TEXT, bot_handle TEXT, bot_avatar TEXT,
            note_classification TEXT, source_note_id TEXT,
            reply_likes INTEGER, reply_reposts INTEGER, reply_views INTEGER,
            is_stub INTEGER, PRIMARY KEY (post_id, condition))""")

    n_iv = 0
    for p in posts:
        p["media_json"] = json.dumps(media.get(p["post_id"], []))
        conn.execute(
            "INSERT INTO posts VALUES (:post_id,:content,:created_at,:author_name,"
            ":author_handle,:author_verified,:likes,:reposts,:views,"
            ":polarity_condition,:topic_condition,:media_json)", p)

        re = synth_engagement(p["post_id"], "reply")   # identical across tones
        post_replies = replies.get(p["post_id"], {})
        for tone in TONES:
            real = post_replies.get(tone)
            body = real if real else f"[STUB — {tone} reply pending generation]"
            is_stub = 0 if real else 1
            conn.execute("INSERT INTO interventions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                p["post_id"], tone, "bot_reply", body,
                BOT_NAME, BOT_HANDLE, BOT_AVATAR, None, None,
                re["likes"], re["reposts"], re["views"], is_stub))
            n_iv += 1

        note = notes.get(p["post_id"])
        body = note["summary"] if note else ""
        conn.execute("INSERT INTO interventions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            p["post_id"], "control", "community_note", body,
            None, None, None,
            note["classification"] if note else None,
            note["noteId"] if note else None,
            None, None, None, 0))
        n_iv += 1

    conn.commit()
    conn.close()
    os.replace(tmp_db, out_db)
    return len(posts), n_iv


def main():
    ap = argparse.ArgumentParser(description="Build the mock-X study.db.")
    ap.add_argument("--selected", default=DEFAULT_SELECTED)
    ap.add_argument("--notes", default=DEFAULT_NOTES_CSV)
    ap.add_argument("--media", default=DEFAULT_MEDIA_CSV)
    ap.add_argument("--replies", default=DEFAULT_REPLIES_CSV)
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()
    n_posts, n_iv = build(args.selected, args.notes, args.db,
                          media_csv=args.media, replies_csv=args.replies)
    print(f"Wrote {n_posts} posts, {n_iv} interventions to {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
