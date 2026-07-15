"""Video path (T9) — keyframe extraction, aggregation, Stage-1.5 wiring."""
import shutil
from pathlib import Path
from unittest import mock

import pytest

from agent.factcheck import video
from agent.factcheck.multimodal import ImageEvidence, MultimodalExtraction
from agent.factcheck.schema import CanonicalImageMatch

_REPO = Path(__file__).resolve().parent.parent
_SAMPLE_MP4 = next((_REPO / "study" / "data" / "media").glob("*/0.mp4"), None) \
    if (_REPO / "study" / "data" / "media").is_dir() else None
_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_extract_keyframes_missing_file_returns_empty():
    assert video.extract_keyframes("/no/such/file.mp4") == []


def test_aggregate_dedups_ocr_and_labels_frames():
    exs = [
        MultimodalExtraction(ocr_text="BREAKING NEWS", description="anchor at desk",
                             search_hint="cnn breaking news clip"),
        MultimodalExtraction(ocr_text="BREAKING NEWS", description="same chyron, wider shot",
                             search_hint=""),
        MultimodalExtraction(ocr_text="Dow +500", description="stock ticker",
                             search_hint="dow jones 500 point gain"),
    ]
    ocr, desc, hint, canonical = video._aggregate(exs)
    assert ocr.count("BREAKING NEWS") == 1          # deduped across frames
    assert "Dow +500" in ocr
    assert "[frame 1]" in desc and "[frame 3]" in desc
    assert "sampled video keyframes" in desc
    assert hint == "cnn breaking news clip"          # first non-empty, temporal order
    assert canonical is None


def test_aggregate_prefers_high_confidence_canonical():
    low = CanonicalImageMatch(name="some clip", confidence="low")
    high = CanonicalImageMatch(name="Tank Man 1989", confidence="high")
    exs = [
        MultimodalExtraction(ocr_text="", description="d1", search_hint="", canonical_image_match=low),
        MultimodalExtraction(ocr_text="", description="d2", search_hint="", canonical_image_match=high),
    ]
    _, _, _, canonical = video._aggregate(exs)
    assert canonical is not None and canonical.confidence == "high"


def test_extract_video_none_when_no_frames():
    with mock.patch("agent.factcheck.video.extract_keyframes", return_value=[]):
        assert video.extract_video("/x.mp4", search_backend=mock.MagicMock()) is None


def test_extract_video_aggregates_and_searches():
    frames = [b"\xff\xd8frame1", b"\xff\xd8frame2"]
    exs = [
        MultimodalExtraction(ocr_text="TEXT A", description="scene A",
                             search_hint="provenance query"),
        MultimodalExtraction(ocr_text="TEXT B", description="scene B", search_hint=""),
    ]
    backend = mock.MagicMock()
    backend.search.return_value = []
    with mock.patch("agent.factcheck.video.extract_keyframes", return_value=frames), \
         mock.patch("agent.factcheck.video._vlm_extract", side_effect=exs):
        ev = video.extract_video("/clip.mp4", search_backend=backend)
    assert isinstance(ev, ImageEvidence)
    assert ev.image_url == "/clip.mp4"
    assert "TEXT A" in ev.ocr_text and "TEXT B" in ev.ocr_text
    assert ev.search_hint == "provenance query"
    backend.search.assert_called_once()              # provenance search fired on the hint


def test_extract_video_survives_per_frame_vlm_failure():
    frames = [b"\xff\xd8a", b"\xff\xd8b"]
    good = MultimodalExtraction(ocr_text="GOOD", description="d", search_hint="")
    backend = mock.MagicMock(); backend.search.return_value = []
    with mock.patch("agent.factcheck.video.extract_keyframes", return_value=frames), \
         mock.patch("agent.factcheck.video._vlm_extract",
                    side_effect=[ValueError("bad json"), good]):
        ev = video.extract_video("/clip.mp4", search_backend=backend)
    assert ev is not None and "GOOD" in ev.ocr_text  # one frame failed, one survived


@pytest.mark.skipif(not (_HAS_FFMPEG and _SAMPLE_MP4),
                    reason="ffmpeg or sample mp4 unavailable")
def test_extract_keyframes_real_mp4_returns_jpegs():
    frames = video.extract_keyframes(str(_SAMPLE_MP4), n=3)
    assert 1 <= len(frames) <= 3
    for f in frames:
        assert f[:2] == b"\xff\xd8"                  # JPEG SOI marker
