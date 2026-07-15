"""Stage 1.5 for video posts (v0.7 T9).

41 of the 108 study posts are videos whose claim lives in the clip — on-screen
text/chyrons, the depicted scene, recycled or AI-generated footage. The
text/photo pipeline can't see any of it. This module samples keyframes with
ffmpeg, runs each through the SAME VLM extractor the image path uses
(`multimodal._vlm_extract`), and aggregates the frames into a single
`ImageEvidence` so the loop, reconcile, and freeze consume video evidence
through the existing image plumbing unchanged.

Keyframe-only by design (per the 2026-07-14 scope decision): captures visual
+ on-screen-text claims. Spoken-audio transcription is a documented follow-up.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import anthropic

from .multimodal import ImageEvidence, MultimodalExtraction, _vlm_extract
from .schema import CanonicalImageMatch
from .search import SearchBackend, SearchHit

logger = logging.getLogger(__name__)

_DEFAULT_KEYFRAMES = 5
# Per-frame VLM already caps at low effort; keep the whole video extraction
# bounded so one slow clip can't stall a batch post.
_FFMPEG_TIMEOUT_S = 90.0
_OCR_JOIN_CAP = 4000
_DESC_JOIN_CAP = 4000


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_keyframes(
    video_path: str, *, n: int = _DEFAULT_KEYFRAMES, timeout_s: float = _FFMPEG_TIMEOUT_S,
) -> list[bytes]:
    """Sample `n` evenly-spaced JPEG keyframes from a local video file.

    Uses ffmpeg's `thumbnail` filter over `n` segments so each frame is the
    most representative of its slice (better than fixed-interval grabs for
    catching chyrons/cuts). Returns JPEG byte blobs in temporal order; empty
    list on any ffmpeg failure (caller degrades gracefully).
    """
    src = Path(video_path)
    if not src.is_file():
        logger.warning("video: file not found %s", video_path)
        return []
    if not ffmpeg_available():
        logger.warning("video: ffmpeg not on PATH; cannot extract keyframes")
        return []

    with tempfile.TemporaryDirectory() as td:
        out_pat = str(Path(td) / "kf_%03d.jpg")
        # thumbnail=n picks one representative frame per n-frame window; we then
        # keep at most `n` frames spread across the clip. `-vsync vfr` avoids
        # duplicate padding frames.
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src),
            "-vf", f"thumbnail=n=100,select='not(mod(n\\,{n}))'",
            "-frames:v", str(n), "-vsync", "vfr", out_pat,
        ]
        try:
            subprocess.run(cmd, timeout=timeout_s, check=True,
                           capture_output=True)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("video: ffmpeg keyframe extraction failed for %s: %s",
                           video_path, str(exc)[:200])
            # Fallback: a single frame at 1s in — better than nothing.
            return _single_frame_fallback(src, timeout_s)
        frames = sorted(Path(td).glob("kf_*.jpg"))
        blobs = [f.read_bytes() for f in frames if f.stat().st_size > 0]
        if blobs:
            return blobs[:n]
        return _single_frame_fallback(src, timeout_s)


def _single_frame_fallback(src: Path, timeout_s: float) -> list[bytes]:
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "one.jpg")
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "1",
               "-i", str(src), "-frames:v", "1", out]
        try:
            subprocess.run(cmd, timeout=timeout_s, check=True, capture_output=True)
        except (subprocess.SubprocessError, OSError):
            return []
        p = Path(out)
        return [p.read_bytes()] if p.is_file() and p.stat().st_size > 0 else []


def _aggregate(extractions: list[MultimodalExtraction]) -> tuple[str, str, str, Optional[CanonicalImageMatch]]:
    """Fold per-frame VLM results into one video-level (ocr, description,
    search_hint, canonical_match)."""
    ocr_parts: list[str] = []
    desc_parts: list[str] = []
    seen_ocr: set[str] = set()
    for i, ex in enumerate(extractions, 1):
        ocr = (ex.ocr_text or "").strip()
        if ocr and ocr.lower() not in seen_ocr:      # dedup repeated chyrons across frames
            seen_ocr.add(ocr.lower())
            ocr_parts.append(ocr)
        desc = (ex.description or "").strip()
        if desc:
            desc_parts.append(f"[frame {i}] {desc}")
    ocr_text = "\n".join(ocr_parts)[:_OCR_JOIN_CAP]
    description = ("[sampled video keyframes]\n" + "\n".join(desc_parts))[:_DESC_JOIN_CAP]
    # Best search hint: first non-empty (frame order is temporal; the opening
    # frame usually carries the setup/caption).
    search_hint = next((ex.search_hint.strip() for ex in extractions
                        if (ex.search_hint or "").strip()), "")
    # Strongest canonical match across frames, if any (prefer high confidence).
    canonical = None
    for ex in extractions:
        cm = ex.canonical_image_match
        if cm is None:
            continue
        if canonical is None or (cm.confidence == "high" and canonical.confidence != "high"):
            canonical = cm
    return ocr_text, description, search_hint, canonical


def extract_video(
    video_path: str,
    *,
    search_backend: SearchBackend,
    n_keyframes: int = _DEFAULT_KEYFRAMES,
    provenance_top_k: int = 5,
) -> Optional[ImageEvidence]:
    """Stage 1.5 for one local video file. Returns a single ImageEvidence
    aggregating `n_keyframes` VLM reads, or None when no frame could be
    read/analyzed."""
    logger.info("Stage 1.5 (video): extracting %s", video_path)
    frames = extract_keyframes(video_path, n=n_keyframes)
    if not frames:
        logger.warning("video: no keyframes for %s — skipping", video_path)
        return None

    extractions: list[MultimodalExtraction] = []
    for idx, blob in enumerate(frames):
        try:
            extractions.append(_vlm_extract(blob, "image/jpeg"))
        except (ValueError, TimeoutError, anthropic.APIConnectionError) as exc:
            logger.warning("video: VLM failed on frame %d of %s: %s", idx, video_path, exc)
    if not extractions:
        return None

    ocr_text, description, search_hint, canonical = _aggregate(extractions)

    provenance_hits: tuple[SearchHit, ...] = ()
    if search_hint:
        try:
            provenance_hits = tuple(search_backend.search(search_hint, top_k=provenance_top_k))
        except Exception as exc:  # provenance is best-effort; frame OCR/desc still useful
            logger.warning("video: provenance search failed for %s: %s", video_path, exc)

    logger.info(
        "Stage 1.5 (video) done: %s frames=%d ocr_chars=%d desc_chars=%d provenance=%d",
        video_path, len(extractions), len(ocr_text), len(description), len(provenance_hits),
    )
    return ImageEvidence(
        image_url=video_path,
        ocr_text=ocr_text,
        description=description,
        search_hint=search_hint,
        provenance_hits=provenance_hits,
        canonical_image_match=canonical,
    )
