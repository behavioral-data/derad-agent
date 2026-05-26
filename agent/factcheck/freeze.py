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

from .schema import Action, ActionOutcome, FrozenVerdict, OverallState, PresentationPayload


_DEFAULT_FREEZE_ROOT = Path(__file__).resolve().parents[2] / "data" / "freezes"


@dataclass(frozen=True)
class RendererView:
    """The only fields a tone renderer is allowed to see (design §3.2).

    `action`, `action_outcome`, and `overall_state` are exposed as pipeline
    metadata — they select WHICH template the renderer uses but don't leak
    reasoning content (consolidated_findings, evidence_stances, raw lens
    narratives stay off-limits).

    `pivoted_from` + `invoker_instruction_text` give the renderer enough
    context to weave a one-clause pivot disclosure into the reply when the
    invoker's explicit ask doesn't match the action taken. Disclosure is
    the renderer's job — it owns the char budget and can phrase the
    clarification within the same 256-weighted-char envelope.
    """

    presentation_payload: PresentationPayload
    tone_neutral_justification: str
    action: Action = "verify"
    action_outcome: ActionOutcome = "verified_nei"
    overall_state: OverallState = "checked"
    pivoted_from: Optional[Action] = None
    invoker_instruction_text: str = ""


def view_for_renderer(frozen: FrozenVerdict) -> RendererView:
    """Project the frozen object down to the renderer's allowed fields."""
    return RendererView(
        presentation_payload=frozen.presentation_payload,
        tone_neutral_justification=frozen.tone_neutral_justification,
        action=frozen.action,
        action_outcome=frozen.action_outcome,
        overall_state=frozen.overall_state,
        pivoted_from=frozen.pivoted_from,
        invoker_instruction_text=frozen.invoker_instruction_text,
    )


def freeze_to_disk(frozen: FrozenVerdict, root: Optional[Path] = None) -> Path:
    """Write the frozen verdict to `<root>/<invocation_id>.json`. Returns the path."""
    target_root = root or _DEFAULT_FREEZE_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    path = target_root / f"{frozen.invocation_id}.json"
    path.write_text(frozen.model_dump_json(indent=2))
    return path
