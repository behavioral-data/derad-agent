"""Tests for mockx.fetch_media pure helpers (parse_media, index_media)."""
from __future__ import annotations

from study.interface.fetch_media import index_media, parse_media


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
        {"type": "photo", "url": "https://x/a.jpg", "video_url": None},
        {"type": "photo", "url": "https://x/b.jpg", "video_url": None},
    ]


def test_parse_media_video_uses_preview_frame():
    mbk = {"k1": {"media_key": "k1", "type": "video",
                  "preview_image_url": "https://x/prev.jpg"}}
    tweet = {"attachments": {"media_keys": ["k1"]}}
    out = parse_media(tweet, mbk)
    # no variants → poster only, no playable mp4
    assert out == [{"type": "video", "url": "https://x/prev.jpg", "video_url": None}]


def test_parse_media_video_picks_best_mp4_variant():
    mbk = {"k1": {"media_key": "k1", "type": "video",
                  "preview_image_url": "https://x/prev.jpg",
                  "variants": [
                      {"content_type": "application/x-mpegURL", "url": "https://x/hls.m3u8"},
                      {"content_type": "video/mp4", "bit_rate": 256000, "url": "https://x/low.mp4"},
                      {"content_type": "video/mp4", "bit_rate": 832000, "url": "https://x/mid.mp4"},
                      {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://x/high.mp4"},
                  ]}}
    tweet = {"attachments": {"media_keys": ["k1"]}}
    out = parse_media(tweet, mbk)
    # highest-bitrate mp4 at or below the 1 Mbps cap → the 832k variant
    assert out == [{"type": "video", "url": "https://x/prev.jpg", "video_url": "https://x/mid.mp4"}]


def test_parse_media_skips_media_without_usable_url():
    mbk = {"k1": {"media_key": "k1", "type": "video"}}  # no preview_image_url
    tweet = {"attachments": {"media_keys": ["k1"]}}
    assert parse_media(tweet, mbk) == []


def test_parse_media_no_attachments():
    assert parse_media({"text": "hi"}, {}) == []
