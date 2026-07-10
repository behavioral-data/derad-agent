"""DraftVerdict — the loop's `finalize` tool schema — and the pure
assembler that maps a draft + evidence log onto the FrozenVerdict spine."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .loop_tools import EvidenceRow
from .schema import (
    Action, BackendVersion, ChallengedProposition, Claim, ConsolidatedFindings,
    ContextualFinding, Counterpoint, CrossModalReport, CitableSource, Evidence,
    FrozenVerdict, Lens1, Perspective, PresentationPayload, RefutedProposition,
    SourceQualityEntry, TierRef, UnaddressedProposition, VerifiedProposition,
    VerifierReport,
)
from .sources import build_quality_table, source_lists_version
from .verdict import derive_action_outcome, derive_verdict
from .prompt_store import prompt_version


class EvidenceRef(BaseModel):
    row: int
    stance: Literal["supports", "refutes", "neutral"] = "neutral"
    on_point: bool = False


class DraftSource(BaseModel):
    url: str
    display_name: str


class DraftVerdict(BaseModel):
    hypotheses: list[str] = Field(default_factory=list)
    target_hypothesis: str = ""
    implied_claim: str = ""
    action: Action = "verify"
    central_claim: str
    headline_finding: str
    justification: str
    counter_fact: Optional[str] = None
    context_note: Optional[str] = None
    counterpoints: list[dict] = Field(default_factory=list)   # {"summary", "source_urls"}
    perspectives: list[dict] = Field(default_factory=list)    # {"label", "summary", "source_urls"}
    primary_sources: list[DraftSource] = Field(default_factory=list)
    load_bearing_evidence_snippet: str = ""
    load_bearing_facts: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    knowledge_state_at_post_date: str = ""
    verdict_derivation: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    verdict_leaning: Literal["supported", "refuted", "conflicting", "insufficient"] = "insufficient"


def _tier_refs(urls: list[str], table: list[SourceQualityEntry]) -> tuple[TierRef, ...]:
    tier_by = {e.url: e.tier for e in table}
    return tuple(TierRef(url=u, tier=tier_by.get(u, "unknown")) for u in urls)


def _findings_for(draft: DraftVerdict, ref_urls: dict[str, list[str]],
                  table: list[SourceQualityEntry]) -> ConsolidatedFindings:
    """Build the action-appropriate central bucket from the draft."""
    if draft.action == "provide_context":
        return ConsolidatedFindings(contextual_findings=(
            ContextualFinding(topic=draft.central_claim,
                              missing_context=draft.context_note or draft.justification,
                              citing_sources=_tier_refs(ref_urls["primary"], table),
                              is_central=True),
        ))
    if draft.action == "challenge_opinion":
        cps = tuple(
            Counterpoint(summary=c.get("summary", ""),
                         citing_sources=_tier_refs(c.get("source_urls", []), table))
            for c in draft.counterpoints
        ) or (Counterpoint(summary=draft.justification,
                           citing_sources=_tier_refs(ref_urls["primary"], table)),)
        return ConsolidatedFindings(challenged_propositions=(
            ChallengedProposition(proposition=draft.central_claim,
                                  counterpoints=cps, is_central=True),
        ))
    if draft.action == "surface_perspectives":
        ps = tuple(
            Perspective(label=p.get("label", ""), summary=p.get("summary", ""),
                        citing_sources=_tier_refs(p.get("source_urls", []), table))
            for p in draft.perspectives
        )
        return ConsolidatedFindings(
            perspectives=ps,
            unaddressed_propositions=(UnaddressedProposition(
                proposition=draft.central_claim,
                reason="evidence retrieved but silent", is_central=True),),
        )
    # verify (and decline falls through in pipeline_loop before assemble)
    refs = _tier_refs(ref_urls["primary"], table)
    if draft.verdict_leaning == "refuted":
        return ConsolidatedFindings(refuted_propositions=(
            RefutedProposition(proposition=draft.central_claim, refuting_sources=refs,
                               counter_fact=draft.counter_fact or draft.headline_finding,
                               is_central=True),
        ))
    if draft.verdict_leaning == "supported":
        return ConsolidatedFindings(verified_propositions=(
            VerifiedProposition(proposition=draft.central_claim,
                                supporting_sources=refs, is_central=True),
        ))
    return ConsolidatedFindings(unaddressed_propositions=(
        UnaddressedProposition(proposition=draft.central_claim,
                               reason="evidence retrieved but silent", is_central=True),
    ))


def assemble_frozen(
    draft: DraftVerdict,
    rows: list[EvidenceRow],
    *,
    invocation_id: str,
    invocation_time: datetime,
    target_tweet_id: str,
    backend_name: str,
    thread_context_str: str = "",
    modality: str = "text",
    attached_images: tuple = (),
    as_of: Optional[datetime] = None,
    evidence_cutoff: Optional[datetime] = None,
    verifier_report: Optional[VerifierReport] = None,
) -> FrozenVerdict:
    by_idx = {r.idx: r for r in rows}
    refs = [er for er in draft.evidence_refs if er.row in by_idx]
    evidence = tuple(
        Evidence(question=draft.target_hypothesis or draft.central_claim,
                 source_url=by_idx[er.row].url,
                 snippet=by_idx[er.row].snippet or by_idx[er.row].title,
                 stance=er.stance,
                 body_markdown=by_idx[er.row].body_markdown,
                 published_at=by_idx[er.row].published_at,
                 origin=by_idx[er.row].origin,       # type: ignore[arg-type]
                 via_snapshot=by_idx[er.row].via_snapshot)
        for er in refs
    )
    all_urls = [by_idx[er.row].url for er in refs] + [s.url for s in draft.primary_sources]
    table = build_quality_table(all_urls)
    primary_urls = [s.url for s in draft.primary_sources]
    ref_urls = {"primary": primary_urls or [e.source_url for e in evidence][:3]}
    findings = _findings_for(draft, ref_urls, table)
    on_point = frozenset(by_idx[er.row].url for er in refs if er.on_point)
    action_outcome = derive_action_outcome(draft.action, findings, table, on_point_urls=on_point)
    payload = PresentationPayload(
        headline_finding=draft.headline_finding,
        counter_fact=draft.counter_fact,
        primary_sources_to_cite=tuple(
            CitableSource(url=s.url, display_name=s.display_name) for s in draft.primary_sources),
        load_bearing_evidence_snippet=draft.load_bearing_evidence_snippet,
        context_note=draft.context_note,
        counterpoints=findings.challenged_propositions[0].counterpoints
            if findings.challenged_propositions else (),
        perspectives=findings.perspectives,
        load_bearing_facts=tuple(draft.load_bearing_facts),
    )
    return FrozenVerdict(
        invocation_id=invocation_id,
        target_tweet_id=target_tweet_id,
        invocation_time=invocation_time,
        thread_context=thread_context_str,
        modality=modality,   # type: ignore[arg-type]
        backend_version=BackendVersion(
            model="claude-via-azure-ai-services",
            search_provider=backend_name,
            prompt_version=prompt_version(),
            source_reliability_lists_version=source_lists_version(),
        ),
        attached_images=tuple(attached_images),
        claims=(Claim(claim_id="c1", text=draft.central_claim, type="verifiable",
                      is_central=True, evidence=evidence),),
        cross_modal_report=CrossModalReport(
            lens_1_text_text=Lens1(narrative=draft.verdict_derivation or draft.justification)),
        consolidated_findings=findings,
        source_quality_table=tuple(table),
        action=draft.action,
        action_source="inferred",
        action_outcome=action_outcome,
        verdict_label=derive_verdict(findings, table),
        tone_neutral_justification=draft.justification,
        presentation_payload=payload,
        overall_state="checked",
        engine="loop",
        hypotheses=tuple(draft.hypotheses),
        target_hypothesis=draft.target_hypothesis,
        implied_claim=draft.implied_claim,
        knowledge_state_at_post_date=draft.knowledge_state_at_post_date,
        verdict_derivation=draft.verdict_derivation,
        as_of=as_of,
        evidence_cutoff=evidence_cutoff,
        verifier_report=verifier_report,
    )
