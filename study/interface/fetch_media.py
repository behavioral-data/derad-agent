"""One-time: download the media attached to each study tweet from X.

Uses the project's X client (OAuth1, creds in agent/llm/.env) to resolve media
for the study tweetIds, downloads the image bytes into study/interface/static/media/,
and writes a small committed index (study/interface/data/media_index.csv) consumed by build_db.

Photos download their full image; videos/animated_gifs download their preview
frame (still). Run once; the downloaded files + index are committed so the
study stimulus is preserved and served locally (no runtime dependency on X).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.parse

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../study/interface
_STUDY = os.path.dirname(_HERE)                              # .../study
_ROOT = os.path.dirname(_STUDY)                              # repo root
DEFAULT_SELECTED = os.path.join(_STUDY, "posts", "selected_posts.csv")
DEFAULT_MEDIA_DIR = os.path.join(_HERE, "static", "media")
DEFAULT_OUT = os.path.join(_HERE, "data", "media_index.csv")

csv.field_size_limit(10_000_000)
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def index_media(includes):
    """media_key -> media object, from an API response's `includes`."""
    out = {}
    media = includes.get("media") if isinstance(includes, dict) else None
    for m in media or []:
        if isinstance(m, dict) and m.get("media_key"):
            out[m["media_key"]] = m
    return out


def parse_media(tweet, media_by_key):
    """Ordered downloadable media for one tweet.

    Returns a list of {"type", "url"} in attachment order. Photos use `url`;
    videos / animated_gifs use `preview_image_url` (a still frame). Media with
    no usable URL is skipped.
    """
    out = []
    att = tweet.get("attachments") if isinstance(tweet, dict) else None
    keys = (att.get("media_keys") if isinstance(att, dict) else None) or []
    for k in keys:
        m = media_by_key.get(k)
        if not isinstance(m, dict):
            continue
        mtype = m.get("type")
        url = m.get("url") if mtype == "photo" else m.get("preview_image_url")
        if url:
            out.append({"type": mtype, "url": url})
    return out


def _ext_for(url):
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _PHOTO_EXTS else ".jpg"


def _download(url, dest):
    full = url if "?" in url else url + "?name=large"
    r = requests.get(full, timeout=30)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)


def fetch_all(selected_csv, media_dir, out_csv, client=None):
    if client is None:
        from agent.llm.config import get_x_client
        client = get_x_client()

    ids, seen = [], set()
    for row in csv.DictReader(open(selected_csv, newline="")):
        t = row["tweetId"]
        if t not in seen:
            seen.add(t); ids.append(t)

    rows = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        resp = client.posts.get_by_ids(
            ids=chunk,
            tweet_fields=["attachments"],
            expansions=["attachments.media_keys"],
            media_fields=["url", "preview_image_url", "type"],
        )
        data = getattr(resp, "data", None) or []
        mbk = index_media(getattr(resp, "includes", None) or {})
        for tweet in data:
            if not isinstance(tweet, dict):
                continue
            tid = str(tweet.get("id"))
            for ordinal, media in enumerate(parse_media(tweet, mbk)):
                ext = _ext_for(media["url"])
                rel = os.path.join("media", tid, f"{ordinal}{ext}")
                dest = os.path.join(media_dir, tid, f"{ordinal}{ext}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    _download(media["url"], dest)
                except Exception as e:
                    print(f"  WARN {tid}#{ordinal}: download failed ({e})", file=sys.stderr)
                    continue
                rows.append({"tweetId": tid, "ordinal": ordinal,
                             "type": media["type"], "path": rel.replace(os.sep, "/")})

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tweetId", "ordinal", "type", "path"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Download study-tweet media from X.")
    ap.add_argument("--selected", default=DEFAULT_SELECTED)
    ap.add_argument("--media-dir", default=DEFAULT_MEDIA_DIR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    n = fetch_all(args.selected, args.media_dir, args.out)
    print(f"Wrote {n} media files; index at {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
