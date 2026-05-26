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

# What the bot is being asked (or has decided) to do for one mention.
# The action drives Stage 4 search strategy, Stage 4.5 reconcile prompt,
# Stage 7 render template selection, and the freeze record.
Action = Literal[
    "verify",                # verifiable claim → check against evidence
    "provide_context",       # claim literally true but misleadingly framed
    "challenge_opinion",     # strong opinion → push back with counter-evidence
    "surface_perspectives",  # contested topic → present multiple credible sides
    "decline",               # nothing actionable; bot says so politely
]

# Where the action came from. "explicit" = invoker said so in the mention.
# "inferred" = invoker said nothing and the model picked one from the claim
# character. "explicit_but_unactionable" = invoker asked for one action but
# the claim doesn't support it; the bot pivoted silently to a fitting
# action and discloses the pivot in the rendered reply.
ActionSource = Literal["explicit", "inferred", "explicit_but_unactionable"]

# Terminal outcome of the chosen action — replaces the verify-specific
# Verdict literal across the unified analytics surface.
ActionOutcome = Literal[
    # verify (1:1 with old Verdict)
    "verified_supported",
    "verified_refuted",
    "verified_conflicting",
    "verified_nei",
    # provide_context
    "context_provided",
    "context_unavailable",
    # challenge_opinion
    "challenged",
    "challenge_unavailable",
    # surface_perspectives
    "perspectives_surfaced",
    "perspectives_insufficient",
    # decline
    "declined",
]


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


class CanonicalImageMatch(_Frozen):
    """Meta-recognition of the photograph itself as a famous artifact.

    Populated when the VLM recognizes the image as a canonical /
    widely-circulated photograph (Tank Man, the Apollo 11 Buzz Aldrin
    photo, the AI-generated Pope Balenciaga image, etc.) — not just what
    is depicted but the photograph's identity as an artifact.
    """
    name: str                                                    # "Tank Man, Tiananmen Square, June 1989"
    confidence: Literal["high", "medium", "low"]                 # high only for truly famous images
    known_context: str = ""                                      # one-paragraph factual context the photo is famous for
    known_misuses: str = ""                                      # short note when image is commonly recirculated misleadingly


class AttachedImage(_Frozen):
    image_id: str
    image_url: str
    ocr_text: str = ""
    vlm_description: str = ""
    canonical_image_match: Optional[CanonicalImageMatch] = None
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


class Counterpoint(_Frozen):
    """One distinct counter-argument against a strongly-stated opinion,
    grounded in sources from source_quality_table."""
    summary: str                                            # ≤160 chars; one-sentence counter
    citing_sources: tuple[TierRef, ...]                     # at least one URL in source_quality_table
    weight: Literal["strong", "moderate", "weak"] = "moderate"


class ChallengedProposition(_Frozen):
    """Used when action == challenge_opinion. The central opinion is
    pushed back on with one or more counterpoints."""
    proposition: str
    counterpoints: tuple[Counterpoint, ...]
    is_central: bool


class ContextualFinding(_Frozen):
    """Used when action == provide_context. The literal claim may verify
    against the public record, but the framing leaves out something
    material that changes how a reader should interpret it."""
    topic: str                                              # the claim's surface statement
    missing_context: str                                    # ≤220 chars; the framing the claim hides
    citing_sources: tuple[TierRef, ...]
    is_central: bool


class Perspective(_Frozen):
    """Used when action == surface_perspectives. One distinct credible
    viewpoint on a contested topic."""
    label: str                                              # ≤60 chars; e.g. "Economic-cost view"
    summary: str                                            # ≤200 chars; the view in its own terms
    citing_sources: tuple[TierRef, ...]                     # at least one credible source


class ConsolidatedFindings(_Frozen):
    verified_propositions: tuple[VerifiedProposition, ...] = Field(default_factory=tuple)
    refuted_propositions: tuple[RefutedProposition, ...] = Field(default_factory=tuple)
    disputed_propositions: tuple[DisputedProposition, ...] = Field(default_factory=tuple)
    unaddressed_propositions: tuple[UnaddressedProposition, ...] = Field(default_factory=tuple)
    # Per-action bucket types — populated only when their action runs.
    contextual_findings: tuple[ContextualFinding, ...] = Field(default_factory=tuple)
    challenged_propositions: tuple[ChallengedProposition, ...] = Field(default_factory=tuple)
    perspectives: tuple[Perspective, ...] = Field(default_factory=tuple)


class SourceQualityEntry(_Frozen):
    url: str
    tier: SourceTier
    tier_source: TierSource
    rationale: str


class CitableSource(_Frozen):
    url: str
    display_name: str


class PresentationPayload(_Frozen):
    """Renderer-facing substrate. Stage 7 is restricted to this + tone_neutral_justification.

    Fields are populated conditionally on action:
      - verify: headline_finding (+ counter_fact when refuted) + primary_sources_to_cite
      - provide_context: headline_finding + context_note + primary_sources_to_cite
      - challenge_opinion: headline_finding + counterpoints (each with citing_sources)
      - surface_perspectives: headline_finding + perspectives (each with citing_sources)
      - decline: headline_finding only
    The renderer reads only the fields appropriate to the action.
    """
    headline_finding: str
    counter_fact: Optional[str] = None
    primary_sources_to_cite: tuple[CitableSource, ...] = Field(default_factory=tuple)
    load_bearing_evidence_snippet: str = ""
    # Per-action fields. Empty / None when the action doesn't use them.
    context_note: Optional[str] = None
    counterpoints: tuple[Counterpoint, ...] = Field(default_factory=tuple)
    perspectives: tuple[Perspective, ...] = Field(default_factory=tuple)


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
    # Action taken by the pipeline (selected at Stage 2+3). Drives the
    # renderer template; together with action_outcome supersedes the
    # verify-only verdict_label.
    action: Action = "verify"
    action_source: ActionSource = "inferred"
    pivoted_from: Optional[Action] = None
    invoker_instruction_text: str = ""
    action_outcome: ActionOutcome = "verified_nei"
    # Legacy verify-only label — kept transitionally so downstream code
    # that hasn't been migrated to action_outcome still compiles. Will be
    # dropped in the cleanup commit once derive_action_outcome is wired.
    verdict_label: Verdict
    tone_neutral_justification: str
    presentation_payload: PresentationPayload
    overall_state: OverallState = "checked"
    frozen: Literal[True] = True
