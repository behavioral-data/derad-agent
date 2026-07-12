"""v0.7 orchestrator: images → verified loop → assemble → freeze.
The legacy staged pipeline (pipeline.py) is untouched; engine selection
happens in agent/app/utils.run_factcheck via DERAD_FACTCHECK_ENGINE."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .context import PipelineContext
from .draft import assemble_frozen
from .freeze import freeze_to_disk
from .pipeline import _attached_image_records, _resolve_modality, _run_multimodal, _thread_context
from .search import build_default_backend
from .verifier import run_verified_loop

logger = logging.getLogger(__name__)


def run_pipeline_loop(
    claim_text: str,
    *,
    target_tweet_id: str = "",
    image_urls: Optional[list[str]] = None,
    tweet_context: Optional[dict] = None,
    invoker_instruction: str = "",
    as_of: Optional[datetime] = None,
    evidence_cutoff: Optional[datetime] = None,
    freeze_root: Optional[Path] = None,
    client=None,
    model: Optional[str] = None,
):
    invocation_id = str(uuid.uuid4())
    invocation_time = datetime.now(timezone.utc)
    image_urls = image_urls or []
    image_evidence = []
    if image_urls:
        image_evidence = _run_multimodal(image_urls, build_default_backend())
    ctx = PipelineContext(tweet_context=tweet_context, image_evidence=image_evidence,
                          invoker_instruction=invoker_instruction or "")
    logger.info("run_pipeline_loop[%s]: starting (study=%s)", invocation_id,
                evidence_cutoff is not None)

    draft, runtime, report, stats = run_verified_loop(
        claim_text, client=client, ctx=ctx, as_of=as_of, cutoff=evidence_cutoff,
        model=model)

    if draft is None:
        # Loop never finalized — freeze an honest NEI record. All 9 decision
        # fields on DraftVerdict are required (Task-6 ruling), so pass them
        # explicitly rather than relying on defaults.
        from .draft import DraftVerdict
        draft = DraftVerdict(
            hypotheses=[], target_hypothesis="", action="verify",
            central_claim=claim_text[:280],
            headline_finding="Not enough reliable evidence to verify this claim.",
            justification="The evidence loop did not produce a verdict within budget.",
            primary_sources=[], load_bearing_facts=[], evidence_refs=[],
            verdict_derivation="", confidence="low", verdict_leaning="insufficient",
        )

    frozen = assemble_frozen(
        draft, runtime.rows,
        invocation_id=invocation_id,
        invocation_time=invocation_time,
        target_tweet_id=target_tweet_id,
        backend_name="loop:web_search+fetch_page",
        thread_context_str=_thread_context(tweet_context),
        modality=_resolve_modality(image_urls, claim_text),
        attached_images=tuple(_attached_image_records(image_evidence)),
        as_of=as_of,
        evidence_cutoff=evidence_cutoff,
        verifier_report=report,
    )
    freeze_to_disk(frozen, root=freeze_root)
    logger.info("run_pipeline_loop[%s]: done (outcome=%s, turns=%s)",
                invocation_id, frozen.action_outcome, getattr(stats, "turns", "?"))
    return frozen
