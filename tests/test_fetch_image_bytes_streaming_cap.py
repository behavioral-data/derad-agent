"""Regression test: fetch_image_bytes must reject an oversized image by
aborting the stream once the running total exceeds _IMAGE_BYTE_CAP — not by
buffering the whole response body first (`resp.content`) and checking the
length afterward. The latter defeats the point of `stream=True`: a
huge/unbounded image would be fully read into memory before the cap check
ever runs, risking an OOM on the worker.
"""
from __future__ import annotations

from agent.factcheck import multimodal as multimodal_module
from agent.factcheck.multimodal import _IMAGE_BYTE_CAP, fetch_image_bytes


class _FakeResponse:
    """Mimics requests.Response for a streamed GET. Tracks whether `.content`
    (whole-body buffering) was ever touched, and how many bytes were
    actually pulled through `.iter_content` before the caller stopped."""

    def __init__(self, total_bytes: int):
        self.headers = {"Content-Type": "image/jpeg"}
        self.status_code = 200
        self._total = total_bytes
        self.content_accessed = False
        self.bytes_yielded = 0
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        remaining = self._total
        while remaining > 0:
            n = min(chunk_size, remaining)
            self.bytes_yielded += n
            yield b"\x00" * n
            remaining -= n

    @property
    def content(self):
        # If fetch_image_bytes ever reads this, it buffered the entire body
        # up front — exactly the bug this test guards against.
        self.content_accessed = True
        return b"\x00" * self._total

    def close(self):
        self.closed = True


def test_oversized_stream_is_rejected_without_full_buffering(monkeypatch):
    # 5x the cap — if the old `resp.content` path ran, this would fully
    # materialize a 100MB+ bytes object before ever checking the length.
    fake = _FakeResponse(total_bytes=_IMAGE_BYTE_CAP * 5)
    monkeypatch.setattr(multimodal_module.requests, "get", lambda *a, **kw: fake)

    result = fetch_image_bytes("https://example.com/huge.jpg")

    assert result is None
    assert fake.content_accessed is False, "whole response body was buffered via .content"
    # Aborted shortly after crossing the cap, not after streaming everything.
    assert fake.bytes_yielded <= _IMAGE_BYTE_CAP + 65536
    assert fake.bytes_yielded < fake._total


def test_within_cap_stream_succeeds(monkeypatch):
    small = _IMAGE_BYTE_CAP // 10
    fake = _FakeResponse(total_bytes=small)
    monkeypatch.setattr(multimodal_module.requests, "get", lambda *a, **kw: fake)

    result = fetch_image_bytes("https://example.com/small.jpg")

    assert result is not None
    data, content_type = result
    assert len(data) == small
    assert content_type == "image/jpeg"
