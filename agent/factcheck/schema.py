"""Pydantic models for the frozen verdict object (design §5.1).

`FrozenVerdict` is the invariance contract: once written at Stage 6 it is
immutable. Downstream stages (renderer at 7, poster at 8) read from it.
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


class BackendVersion(BaseModel):
    model: str
    vlm_model: str = ""
    search_provider: str
    reverse_image_provider: str = ""
    fetch_layer: str = ""
    source_reliability_lists_version: dict = Field(default_factory=dict)
    pipeline_commit: str = ""


class ProvenanceMatch(BaseModel):
    match_url: str
    earliest_seen: Optional[datetime] = None
    match_caption: str = ""


class AttachedImage(BaseModel):
    image_id: str
    image_url: str
    ocr_text: str = ""
    vlm_description: str = ""
    provenance: list[ProvenanceMatch] = Field(default_factory=list)
    manipulation_check: Literal["out_of_scope_nei"] = "out_of_scope_nei"


class Evidence(BaseModel):
    question: str
    source_url: str
    snippet: str
    stance: Stance


class Claim(BaseModel):
    claim_id: str
    text: str
    type: ClaimType
    modality: Modality = "text"
    check_worthy: bool = True
    is_central: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class TierRef(BaseModel):
    url: str
    tier: SourceTier


class CrossSourceContradiction(BaseModel):
    topic: str
    sources_for: list[TierRef]
    sources_against: list[TierRef]
    resolution: str


class Lens1(BaseModel):
    narrative: str
    cross_source_contradictions: list[CrossSourceContradiction] = Field(default_factory=list)


class ImageProvenance(BaseModel):
    earliest_seen: Optional[str] = "unknown"
    true_caption: str = "unknown"
    true_context: str = "unknown"
    provenance_sources: list[str] = Field(default_factory=list)


class Lens2(BaseModel):
    ran: bool = False
    narrative: str = ""
    image_provenance: Optional[ImageProvenance] = None
    image_caption_match: Literal["supports", "contradicts", "undetermined"] = "undetermined"


class ModalityConflict(BaseModel):
    description: str
    text_path_says: str
    image_path_says: str
    weight_of_evidence_favors: Literal["text", "image", "undetermined"]


class Lens3(BaseModel):
    ran: bool = False
    narrative: str = ""
    modality_conflicts: list[ModalityConflict] = Field(default_factory=list)


class CrossModalReport(BaseModel):
    lens_1_text_text: Lens1
    lens_2_image_text: Lens2 = Field(default_factory=Lens2)
    lens_3_cross_modal: Lens3 = Field(default_factory=Lens3)


class VerifiedProposition(BaseModel):
    proposition: str
    supporting_sources: list[TierRef]
    is_central: bool


class RefutedProposition(BaseModel):
    proposition: str
    refuting_sources: list[TierRef]
    counter_fact: str
    is_central: bool


class DisputedProposition(BaseModel):
    proposition: str
    sources_for: list[TierRef]
    sources_against: list[TierRef]
    weight_of_evidence_favors: Literal["for", "against", "undetermined"]
    is_central: bool


class UnaddressedProposition(BaseModel):
    proposition: str
    reason: Literal["no evidence retrieved", "evidence retrieved but silent"]
    is_central: bool


class ConsolidatedFindings(BaseModel):
    verified_propositions: list[VerifiedProposition] = Field(default_factory=list)
    refuted_propositions: list[RefutedProposition] = Field(default_factory=list)
    disputed_propositions: list[DisputedProposition] = Field(default_factory=list)
    unaddressed_propositions: list[UnaddressedProposition] = Field(default_factory=list)


class SourceQualityEntry(BaseModel):
    url: str
    tier: SourceTier
    tier_source: TierSource
    rationale: str


class CitableSource(BaseModel):
    url: str
    display_name: str


class PresentationPayload(BaseModel):
    """Renderer-facing substrate. Stage 7 is restricted to this + tone_neutral_justification."""
    headline_finding: str
    counter_fact: Optional[str] = None
    primary_sources_to_cite: list[CitableSource] = Field(default_factory=list)
    load_bearing_evidence_snippet: str = ""


class FrozenVerdict(BaseModel):
    """Stage 6 freeze object. Immutable once constructed (pydantic frozen=True)."""
    model_config = ConfigDict(frozen=True)

    invocation_id: str
    target_tweet_id: str = ""
    invocation_time: datetime
    thread_context: str = ""
    modality: Modality = "text"
    backend_version: BackendVersion
    attached_images: list[AttachedImage] = Field(default_factory=list)
    claims: list[Claim]
    cross_modal_report: CrossModalReport
    consolidated_findings: ConsolidatedFindings
    source_quality_table: list[SourceQualityEntry]
    verdict_label: Verdict
    tone_neutral_justification: str
    presentation_payload: PresentationPayload
    overall_state: OverallState = "checked"
    frozen: Literal[True] = True
