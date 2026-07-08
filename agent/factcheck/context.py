"""PipelineContext — shared inputs threaded through extract / verify / reconcile.

Bundles the pipeline-wide invariants (tweet metadata, image evidence) so
adding a new shared input (e.g. user_locale) doesn't ripple through three
function signatures. Stage-specific arguments (claim_text, evidence,
source_quality_table) stay as positional args.

Lives in its own module to avoid a circular import — pipeline.py imports
from extract / verify / reconcile, which all import PipelineContext.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .multimodal import ImageEvidence


@dataclass
class PipelineContext:
    tweet_context: Optional[dict] = None
    image_evidence: list[ImageEvidence] = field(default_factory=list)
    # Raw text the invoker wrote in their mention (after stripping the
    # bot handle). Empty string when the invoker only tagged and said
    # nothing — Stage 2+3 then infers the action from the claim itself.
    invoker_instruction: str = ""

    @property
    def image_summaries(self) -> Optional[list[dict]]:
        if not self.image_evidence:
            return None
        return [img.to_prompt_summary() for img in self.image_evidence]
