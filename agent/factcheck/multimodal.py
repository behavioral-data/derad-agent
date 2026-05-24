"""Stage 1.5 — multimodal extraction (design §4.1.5).

Tier 1 (OCR — text in image) and Tier 2 (depicted content) are produced by
a single Claude VLM call. Tier 3 (provenance approximation) is a Bing-
grounded text search on Claude's rich image description — true reverse-
image-search has no Azure-native path post-Bing-API retirement. Tier 4
(manipulation / AI-gen detection) is forced NEI per design.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from pydantic import BaseModel

from agent.llm.config import get_llm

from .search import SearchBackend, SearchHit


logger = logging.getLogger(__name__)


_IMAGE_FETCH_TIMEOUT = 20
_IMAGE_BYTE_CAP = 20 * 1024 * 1024  # 20 MB; pre-resize cap, hard refuse beyond this
_CLAUDE_BASE64_LIMIT = 5 * 1024 * 1024  # Anthropic API max per image (base64-encoded)
# Anthropic measures the base64-encoded payload (~1.33x the raw size). Cap
# raw bytes accordingly so the encoded form fits under the API limit.
_CLAUDE_RAW_LIMIT = int(_CLAUDE_BASE64_LIMIT * 0.74)
_RESIZE_MAX_DIM = 1568  # Claude downsamples larger anyway
_USER_AGENT = (
    "derad-agent/3.0 (fact-checking research bot; "
    "contact: advaitmb@uw.edu)"
)


_VLM_SYSTEM = """You are the multimodal extraction stage of a fact-checking pipeline. The downstream verification step depends on you correctly identifying *who* and *what* is in the image — generic descriptions lose information the fact-checker can't recover.

For each image you receive, produce a structured output with three fields:

1. OCR (`ocr_text`): transcribe every readable string in the image VERBATIM. Preserve line breaks. Include captions, watermarks, on-screen text, headlines, signs, chyrons. Do not paraphrase. If no text, return "".

2. Description (`description`): 2-5 sentences. **Name people, places, events, brands, logos, and other entities whenever you recognize them with reasonable confidence.** Fact-checking REQUIRES these identifications — saying "a man in a suit" when it's clearly Elon Musk, or "a domed building" when it's clearly the U.S. Capitol, destroys the signal that lets us check whether the surrounding claim is true.
    - For named individuals: well-known public figures (politicians, executives, celebrities, athletes, journalists), historical figures, fictional characters with recognizable likenesses.
    - For places: landmarks, named cities, venues, neighborhoods, named geological/architectural features.
    - For events: named protests, summits, disasters, ceremonies, games — if the visual context (banners, settings, crowds, dates) makes it identifiable.
    - For images you recognize as canonical / widely-circulated: name them ("this is the canonical Apollo 11 photo of Buzz Aldrin", "this is the Tank Man photo from Tiananmen 1989"). Note the source/context if you know it.
    - Hedge when genuinely uncertain: say "appears to be X" or "consistent with X" rather than refusing entirely.
    - Only decline to identify when you'd be guessing without grounded visual evidence — and even then, give the most specific distinguishing features (clothing era, setting, signage, distinctive scenery, props) that would help a search engine find the image's context.
    - Avoid interpretation about whether the image is real, edited, or AI-generated — that's Tier 4 (out of scope here).

3. Search hint (`search_hint`): one short paragraph (≤120 words) suitable for pasting into a web search to find articles ABOUT this image or its subject. Use the most distinctive named entities and details from your description. If you named a public figure or known image in (2), use those names here too. No invented facts.

Output a single JSON object that validates against the provided schema.
"""


class MultimodalExtraction(BaseModel):
    """Stage 1.5 output for one image."""

    ocr_text: str = ""
    description: str = ""
    search_hint: str = ""


@dataclass(frozen=True)
class ImageEvidence:
    """Per-image result of Stage 1.5 — wired into the frozen verdict."""

    image_url: str
    ocr_text: str
    description: str
    search_hint: str
    provenance_hits: tuple[SearchHit, ...]


def fetch_image_bytes(url: str) -> Optional[tuple[bytes, str]]:
    """Download image bytes from a URL. Returns (bytes, media_type) or None."""
    try:
        resp = requests.get(
            url,
            timeout=_IMAGE_FETCH_TIMEOUT,
            stream=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except (requests.RequestException, requests.HTTPError):
        logger.exception("Image fetch failed for %s", url)
        return None
    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        logger.warning("Skipping non-image url=%s content-type=%s", url, content_type)
        return None
    data = resp.content
    if len(data) > _IMAGE_BYTE_CAP:
        logger.warning("Image too large (%d bytes), skipping: %s", len(data), url)
        return None
    return data, content_type


_SUPPORTED_VLM_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _shrink_for_claude(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Resize + recompress until under Claude's 5 MB cap. Returns (bytes, media_type)."""
    if len(image_bytes) <= _CLAUDE_RAW_LIMIT and media_type in _SUPPORTED_VLM_MIME:
        return image_bytes, media_type
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; cannot resize. Will attempt as-is.")
        return image_bytes, media_type

    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Convert palette/CMYK/etc to RGB so JPEG encoder is happy.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > _RESIZE_MAX_DIM:
            scale = _RESIZE_MAX_DIM / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Re-encode as JPEG with shrinking quality until we fit.
        for quality in (85, 75, 65, 55, 45):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _CLAUDE_RAW_LIMIT:
                return data, "image/jpeg"
        logger.warning("Could not shrink image below Claude limit; sending best-effort.")
        return data, "image/jpeg"
    except Exception:
        logger.exception("Image resize failed; will attempt as-is.")
        return image_bytes, media_type


def _vlm_extract(image_bytes: bytes, media_type: str) -> MultimodalExtraction:
    """Single Claude VLM call producing OCR + description + search_hint."""
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    image_bytes, media_type = _shrink_for_claude(image_bytes, media_type)
    if media_type not in _SUPPORTED_VLM_MIME:
        logger.warning("Unsupported VLM media type %r; defaulting to image/jpeg", media_type)
        media_type = "image/jpeg"

    schema_json = json.dumps(MultimodalExtraction.model_json_schema(), indent=2)
    system = (
        _VLM_SYSTEM
        + "\n\nRespond with one JSON object validating against this schema. No prose.\n\n"
        + f"Schema:\n```json\n{schema_json}\n```"
    )

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    user_content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        },
        {
            "type": "text",
            "text": "Extract OCR, describe, and emit a search_hint per the schema.",
        },
    ]

    llm = get_llm(reasoning_effort="low", max_tokens=2048)
    response = llm.invoke(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    )
    raw = getattr(response, "content", str(response))
    if isinstance(raw, list):
        text_parts = [b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text"]
        raw = "\n".join(text_parts) if text_parts else json.dumps(raw)

    # Robust JSON extraction.
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError(f"VLM returned no JSON. Head: {raw[:200]}")
        data = json.loads(match.group(0))
    return MultimodalExtraction.model_validate(data)


def extract_image(url: str, *, search_backend: SearchBackend, provenance_top_k: int = 5) -> Optional[ImageEvidence]:
    """Run Stage 1.5 for a single image URL. Returns None if image fetch fails."""
    logger.info("Stage 1.5: extracting image %s", url)
    fetched = fetch_image_bytes(url)
    if fetched is None:
        return None
    image_bytes, media_type = fetched
    try:
        extract = _vlm_extract(image_bytes, media_type)
    except Exception:
        logger.exception("VLM extraction failed for %s", url)
        return None

    provenance_hits: tuple[SearchHit, ...] = ()
    if extract.search_hint.strip():
        try:
            hits = search_backend.search(extract.search_hint, top_k=provenance_top_k)
            provenance_hits = tuple(hits)
        except Exception:
            logger.exception("Provenance search failed for %s", url)

    logger.info(
        "Stage 1.5 done: url=%s ocr_chars=%d desc_chars=%d provenance_hits=%d",
        url, len(extract.ocr_text), len(extract.description), len(provenance_hits),
    )
    return ImageEvidence(
        image_url=url,
        ocr_text=extract.ocr_text,
        description=extract.description,
        search_hint=extract.search_hint,
        provenance_hits=provenance_hits,
    )
