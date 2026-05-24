"""Pydantic models for the frozen verdict object (design §5.1).

`FrozenVerdict` is the invariance contract: once written at Stage 6 it is
immutable. Every nested model is also `frozen=True`, and every collection
on a frozen-tree model is a tuple — together this blocks attribute
reassignment, list-item attribute reassignment, AND `.append`-style
mutation across the whole tree.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


Modality = Literal["text", "image", "mixed"]
ClaimType = Literal["verifiable", "opinion", "mixed"]
Stance = Literal["supports", "refutes", "neutral"]
SourceTier = Literal[
    "fact-checker",
    "reputable-news",
    "primary-source",
    "aggregator",
    "low-quality",
    "satirical",
    "unknown",
]
TierSource = Literal["ifcn", "wikipedia-rsp", "mbfc", "model-prior", "meta-search"]
Verdict = Literal["Supported", "Refuted", "NotEnoughEvidence", "Conflicting"]
OverallState = Literal["checked", "no_checkable_claim"]
Tone = Literal["agreeable", "neutral", "agonistic"]


class _Frozen(BaseModel):
    """Base for every model in the freeze tree — blocks attribute mutation."""
    model_config = ConfigDict(frozen=True)


class BackendVersion(_Frozen):
    model: str
    vlm_model: str = ""
    search_provider: str
    reverse_image_provider: str = ""
    fetch_layer: str = ""
    source_reliability_lists_version: dict = Field(default_factory=dict)
    pipeline_commit: str = ""


class ProvenanceMatch(_Frozen):
    match_url: str
    earliest_seen: Optional[datetime] = None
    match_caption: str = ""


class AttachedImage(_Frozen):
    image_id: str
    image_url: str
    ocr_text: str = ""
    vlm_description: str = ""
    provenance: tuple[ProvenanceMatch, ...] = Field(default_factory=tuple)
    manipulation_check: Literal["out_of_scope_nei"] = "out_of_scope_nei"


class Evidence(_Frozen):
    question: str
    source_url: str
    snippet: str
    stance: Stance


class Claim(_Frozen):
    claim_id: str
    text: str
    type: ClaimType
    modality: Modality = "text"
    check_worthy: bool = True
    is_central: bool = False
    evidence: tuple[Evidence, ...] = Field(default_factory=tuple)


class TierRef(_Frozen):
    url: str
    tier: SourceTier


class CrossSourceContradiction(_Frozen):
    topic: str
    sources_for: tuple[TierRef, ...]
    sources_against: tuple[TierRef, ...]
    resolution: str


class Lens1(_Frozen):
    narrative: str
    cross_source_contradictions: tuple[CrossSourceContradiction, ...] = Field(default_factory=tuple)


class ImageProvenance(_Frozen):
    earliest_seen: Optional[str] = "unknown"
    true_caption: str = "unknown"
    true_context: str = "unknown"
    provenance_sources: tuple[str, ...] = Field(default_factory=tuple)


class Lens2(_Frozen):
    ran: bool = False
    narrative: str = ""
    image_provenance: Optional[ImageProvenance] = None
    image_caption_match: Literal["supports", "contradicts", "undetermined"] = "undetermined"


class ModalityConflict(_Frozen):
    description: str
    text_path_says: str
    image_path_says: str
    weight_of_evidence_favors: Literal["text", "image", "undetermined"]


class Lens3(_Frozen):
    ran: bool = False
    narrative: str = ""
    modality_conflicts: tuple[ModalityConflict, ...] = Field(default_factory=tuple)


class CrossModalReport(_Frozen):
    lens_1_text_text: Lens1
    lens_2_image_text: Lens2 = Field(default_factory=Lens2)
    lens_3_cross_modal: Lens3 = Field(default_factory=Lens3)


class VerifiedProposition(_Frozen):
    proposition: str
    supporting_sources: tuple[TierRef, ...]
    is_central: bool


class RefutedProposition(_Frozen):
    proposition: str
    refuting_sources: tuple[TierRef, ...]
    counter_fact: str
    is_central: bool


class DisputedProposition(_Frozen):
    proposition: str
    sources_for: tuple[TierRef, ...]
    sources_against: tuple[TierRef, ...]
    weight_of_evidence_favors: Literal["for", "against", "undetermined"]
    is_central: bool


class UnaddressedProposition(_Frozen):
    proposition: str
    reason: Literal["no evidence retrieved", "evidence retrieved but silent"]
    is_central: bool


class ConsolidatedFindings(_Frozen):
    verified_propositions: tuple[VerifiedProposition, ...] = Field(default_factory=tuple)
    refuted_propositions: tuple[RefutedProposition, ...] = Field(default_factory=tuple)
    disputed_propositions: tuple[DisputedProposition, ...] = Field(default_factory=tuple)
    unaddressed_propositions: tuple[UnaddressedProposition, ...] = Field(default_factory=tuple)


class SourceQualityEntry(_Frozen):
    url: str
    tier: SourceTier
    tier_source: TierSource
    rationale: str


class CitableSource(_Frozen):
    url: str
    display_name: str


class PresentationPayload(_Frozen):
    """Renderer-facing substrate. Stage 7 is restricted to this + tone_neutral_justification."""
    headline_finding: str
    counter_fact: Optional[str] = None
    primary_sources_to_cite: tuple[CitableSource, ...] = Field(default_factory=tuple)
    load_bearing_evidence_snippet: str = ""


class FrozenVerdict(_Frozen):
    """Stage 6 freeze object. Deep-immutable via _Frozen + tuple collections."""

    invocation_id: str
    target_tweet_id: str = ""
    invocation_time: datetime
    thread_context: str = ""
    modality: Modality = "text"
    backend_version: BackendVersion
    attached_images: tuple[AttachedImage, ...] = Field(default_factory=tuple)
    claims: tuple[Claim, ...]
    cross_modal_report: CrossModalReport
    consolidated_findings: ConsolidatedFindings
    source_quality_table: tuple[SourceQualityEntry, ...]
    verdict_label: Verdict
    tone_neutral_justification: str
    presentation_payload: PresentationPayload
    overall_state: OverallState = "checked"
    frozen: Literal[True] = True
