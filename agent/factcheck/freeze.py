"""Stage 6 — Evidence freeze + Stage 7 invariance boundary.

`freeze_to_disk` writes the immutable verdict object to the freeze store.
`RendererView` is the narrow projection the renderer is allowed to see —
only `presentation_payload` and `tone_neutral_justification`, per the
design's invariance contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .schema import FrozenVerdict, OverallState, PresentationPayload


_DEFAULT_FREEZE_ROOT = Path(__file__).resolve().parents[2] / "data" / "freezes"


@dataclass(frozen=True)
class RendererView:
    """The only fields a tone renderer is allowed to see (design §3.2).

    `overall_state` is exposed as pipeline metadata (not as evidence content)
    so the renderer can take the short-circuit "no checkable claim" path
    without ever seeing reasoning fields.
    """

    presentation_payload: PresentationPayload
    tone_neutral_justification: str
    overall_state: OverallState = "checked"


def view_for_renderer(frozen: FrozenVerdict) -> RendererView:
    """Project the frozen object down to the renderer's allowed fields."""
    return RendererView(
        presentation_payload=frozen.presentation_payload,
        tone_neutral_justification=frozen.tone_neutral_justification,
        overall_state=frozen.overall_state,
    )


def freeze_to_disk(frozen: FrozenVerdict, root: Optional[Path] = None) -> Path:
    """Write the frozen verdict to `<root>/<invocation_id>.json`. Returns the path."""
    target_root = root or _DEFAULT_FREEZE_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    path = target_root / f"{frozen.invocation_id}.json"
    path.write_text(frozen.model_dump_json(indent=2))
    return path
