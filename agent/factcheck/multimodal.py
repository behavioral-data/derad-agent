"""Stage 1.5 — multimodal extraction (design §4.1.5).

Tier 1 (OCR — text in image) and Tier 2 (depicted content) are produced
by a single Claude VLM call. Tier 3 (provenance approximation) is a
web-search text query over Claude's rich image description, using
whatever SearchBackend is configured. Tier 4 (manipulation / AI-gen
detection) is forced NEI per design.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from dataclasses import dataclass
from typing import Optional

import requests
from pydantic import BaseModel

from agent.llm.config import get_llm
from agent.shared.http import BROWSER_USER_AGENT

from .llm import _extract_json
from .schema import CanonicalImageMatch
from .search import SearchBackend, SearchHit


logger = logging.getLogger(__name__)


_IMAGE_FETCH_TIMEOUT = 20
_IMAGE_BYTE_CAP = 20 * 1024 * 1024  # 20 MB; pre-resize cap, hard refuse beyond this
_CLAUDE_BASE64_LIMIT = 5 * 1024 * 1024  # Anthropic API max per image (base64-encoded)
# Anthropic measures the base64-encoded payload (~1.33x the raw size). Cap
# raw bytes accordingly so the encoded form fits under the API limit.
_CLAUDE_RAW_LIMIT = int(_CLAUDE_BASE64_LIMIT * 0.74)
_RESIZE_MAX_DIM = 1568  # Claude downsamples larger anyway


_VLM_SYSTEM = """You are the multimodal extraction stage of a fact-checking pipeline. The downstream verification step depends on you correctly identifying *who* and *what* is in the image — generic descriptions lose information the fact-checker can't recover.

For each image you receive, produce a structured output with four fields:

1. OCR (`ocr_text`): transcribe every readable string in the image VERBATIM. Preserve line breaks. Include captions, watermarks, on-screen text, headlines, signs, chyrons. Do not paraphrase. If no text, return "".

2. Meta-recognition (`canonical_image_match`, OPTIONAL): if this photograph itself is a widely-known canonical artifact — not just an image containing famous things, but a specific famous PHOTO — populate this block. Examples that warrant a `canonical_image_match`:
    - The "Tank Man" photo from Tiananmen Square, June 1989.
    - The Apollo 11 photo of Buzz Aldrin standing on the Moon, July 1969.
    - The AI-generated "Pope Francis in a Balenciaga puffer" image that went viral in March 2023.
    - The "Falling Man" photograph from 9/11.
    - Stock viral images that have been miscaptioned many times (e.g., the Rosa Camfield photo of an elderly woman with her great-granddaughter, often reposted as "101-year-old gives birth to 17th child").
    Fill in:
      - `name`: a short identifying name + date if known (e.g. "Tank Man, Tiananmen Square, June 1989").
      - `confidence`: "high" only when you're certain this is the specific famous photograph (not just a similar-looking scene); "medium" when likely; "low" when it resembles a famous image but you wouldn't bet on it.
      - `known_context`: one paragraph on the photo's documented origin / context / who's in it.
      - `known_misuses`: short note on common ways this image gets miscaptioned or recirculated misleadingly (empty when not applicable).
    Leave the field NULL when the photograph itself is novel / not famous (most images). A photo of a famous person taken yesterday is NOT a canonical image — only the photograph-as-artifact counts.

3. Description (`description`): 2-5 sentences. **Name people, places, events, brands, logos, and other entities whenever you recognize them with reasonable confidence.** Fact-checking REQUIRES these identifications — saying "a man in a suit" when it's clearly Elon Musk, or "a woman with long hair" when it's clearly Nicki Minaj, or "a domed building" when it's clearly the U.S. Capitol, destroys the signal that lets us check whether the surrounding claim is true. Identification is the WHOLE POINT of this step.
    - **For named individuals** — apply this equally across all categories: male and female public figures, executives, politicians, athletes, journalists, AND musicians, actors, actresses, performers, models, influencers. Refusing to identify a female musician you'd identify if she were a male executive is a bias that breaks fact-checking. If you recognize the person, name them.
    - **Hedging is encouraged** when you're not 100% certain. "This appears to be Nicki Minaj" or "consistent with Nicki Minaj based on the styling and features" is FAR more useful to the fact-checker than "a woman with long hair." Default to a hedged identification over a generic one.
    - For places: landmarks, named cities, venues, neighborhoods, named geological/architectural features.
    - For events: named protests, summits, disasters, ceremonies, awards shows, concerts, conferences, games — if visual context (banners, settings, crowds, dates) makes it identifiable.
    - Only decline to identify when you'd be PURE guessing without grounded visual evidence — and even then, give the most specific distinguishing features (clothing era, setting, signage, distinctive scenery, props) that would help a search engine find the image's context.
    - Avoid interpretation about whether the image is real, edited, or AI-generated — that's Tier 4 (out of scope here).

4. Search hint (`search_hint`): one short paragraph (≤120 words) suitable for pasting into a web search to find articles ABOUT this image or its subject. Use the most distinctive named entities and details from your description. When `canonical_image_match` is populated, use that name verbatim in the search hint. No invented facts.

Output a single JSON object that validates against the provided schema.
"""


class MultimodalExtraction(BaseModel):
    """Stage 1.5 output for one image."""

    ocr_text: str = ""
    canonical_image_match: Optional[CanonicalImageMatch] = None
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
    canonical_image_match: Optional[CanonicalImageMatch] = None

    def to_prompt_summary(self) -> dict:
        """4-key dict for prompts that only need image identity/content.
        Includes canonical_image_match when populated so extract sees it."""
        out: dict = {
            "image_url": self.image_url,
            "ocr_text": self.ocr_text,
            "description": self.description,
        }
        if self.canonical_image_match is not None:
            out["canonical_image_match"] = self.canonical_image_match.model_dump()
        return out

    def to_prompt_with_provenance(self) -> dict:
        """Extended dict for reconcile — includes provenance hits + canonical match."""
        out: dict = {
            "image_url": self.image_url,
            "ocr_text": self.ocr_text,
            "description": self.description,
            "provenance_search_hint": self.search_hint,
            "provenance_hits": [
                {"url": h.url, "title": h.title, "snippet": h.snippet}
                for h in self.provenance_hits
            ],
        }
        if self.canonical_image_match is not None:
            out["canonical_image_match"] = self.canonical_image_match.model_dump()
        return out


def fetch_image_bytes(url: str) -> Optional[tuple[bytes, str]]:
    """Download image bytes from a URL. Returns (bytes, media_type) or None."""
    try:
        resp = requests.get(
            url,
            timeout=_IMAGE_FETCH_TIMEOUT,
            stream=True,
            headers={"User-Agent": BROWSER_USER_AGENT},
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
    except (OSError, ValueError) as exc:
        # OSError covers Pillow's UnidentifiedImageError + decoder I/O errors;
        # ValueError covers bad mode / format issues. Unexpected exceptions
        # (MemoryError on giant HEIF, etc.) should propagate.
        logger.warning("Image resize failed (%s); attempting as-is for %s.", exc, media_type)
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

    llm = get_llm(reasoning_effort="low", max_tokens=2048, timeout=60.0)
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

    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"VLM returned no parseable JSON. Head: {raw[:200]}") from exc
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
    except (ValueError, TimeoutError) as exc:
        # ValueError covers JSON parse + schema validation; TimeoutError
        # covers the per-stage budget. Other exceptions propagate.
        logger.warning("VLM extraction failed for %s: %s", url, exc)
        return None

    provenance_hits: tuple[SearchHit, ...] = ()
    if extract.search_hint.strip():
        try:
            hits = search_backend.search(extract.search_hint, top_k=provenance_top_k)
            provenance_hits = tuple(hits)
        except (requests.RequestException, TimeoutError) as exc:
            # Network / search-API failure — the rest of the image evidence
            # is still useful for OCR + description in reconcile.
            logger.warning("Provenance search failed for %s: %s", url, exc)

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
        canonical_image_match=extract.canonical_image_match,
    )
