"""Tests for mockx.fetch_media pure helpers (parse_media, index_media)."""
from __future__ import annotations

from mockx.fetch_media import index_media, parse_media


def test_index_media_keys_by_media_key():
    includes = {"media": [
        {"media_key": "k1", "type": "photo", "url": "https://x/a.jpg"},
        {"media_key": "k2", "type": "video", "preview_image_url": "https://x/b.jpg"},
    ]}
    idx = index_media(includes)
    assert set(idx) == {"k1", "k2"}
    assert idx["k1"]["type"] == "photo"


def test_parse_media_photo_uses_url_in_order():
    mbk = {
        "k1": {"media_key": "k1", "type": "photo", "url": "https://x/a.jpg"},
        "k2": {"media_key": "k2", "type": "photo", "url": "https://x/b.jpg"},
    }
    tweet = {"attachments": {"media_keys": ["k1", "k2"]}}
    out = parse_media(tweet, mbk)
    assert out == [
        {"type": "photo", "url": "https://x/a.jpg"},
        {"type": "photo", "url": "https://x/b.jpg"},
    ]


def test_parse_media_video_uses_preview_frame():
    mbk = {"k1": {"media_key": "k1", "type": "video",
                  "preview_image_url": "https://x/prev.jpg"}}
    tweet = {"attachments": {"media_keys": ["k1"]}}
    out = parse_media(tweet, mbk)
    assert out == [{"type": "video", "url": "https://x/prev.jpg"}]


def test_parse_media_skips_media_without_usable_url():
    mbk = {"k1": {"media_key": "k1", "type": "video"}}  # no preview_image_url
    tweet = {"attachments": {"media_keys": ["k1"]}}
    assert parse_media(tweet, mbk) == []


def test_parse_media_no_attachments():
    assert parse_media({"text": "hi"}, {}) == []
