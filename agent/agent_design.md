# Agonistic Fact-Checking Bot — System Design Specification

## 1. Overview

The system is a user-invoked fact-checking bot deployed on X. A recruited participant summons the bot on a target tweet by replying with a trigger mention. The bot extracts checkable claims, verifies them against live web evidence, reconciles that evidence across modalities, freezes the result, and renders a correction in one of three tones determined by the participant's assigned experimental condition.

The verification backend (Stages 1 through 6) is identical across all three conditions. Only the natural-language rendering at Stage 7 differs. The experimental manipulation is **tone and framing**. The verification logic, retrieved evidence, structured reconciliation, source-quality assessment, and verdict labels are held constant. This invariance is the central design requirement of the system, and most of the engineering effort exists to protect it.

## 2. Goals and Non-Goals

### 2.1 Goals
- Verify factual claims embedded in real tweets at accuracy competitive with the 2024–2026 AVeriTeC/AVerImaTeC shared-task systems (deployable floor: AVeriTeC score 0.477; stretch: 0.63 text-only, 0.55 multimodal).
- Guarantee that the verification backend produces byte-identical structured output regardless of tone condition.
- Render that output in three clearly distinguishable tones that hold factual content constant.
- Post the rendered correction as a reply on X, attributed to a disclosed research bot account.
- Log every artifact needed to reconstruct each fact-check and measure study outcomes.
- Be conservative: prefer "not enough evidence" over a confident wrong verdict.

### 2.2 Non-Goals
- Real-time autonomous monitoring of the timeline. The bot acts only when invoked.
- Manipulating the verification backend across conditions. The backend is frozen.
- Maximizing engagement or virality. Engagement is a measured outcome, not an objective.
- Multi-turn dialogue. The bot delivers a single reply per invocation.
- A numerical confidence score. The system commits to a categorical verdict label backed by a structured evidence object, not to a scalar.
- Bystander belief tracking in v1. Recruited-participant outcomes only.

## 3. Architecture

### 3.1 High-level flow

```
Participant invokes bot on target tweet
            |
            v
[1] Ingestion: fetch target tweet + thread context + attached images
            |
            v
[1.5] Multimodal extraction (if images present)
        - Tier 1: read text in image (VLM/OCR)        -> feeds text path as claims
        - Tier 2: describe depicted content (VLM)     -> "image depicts X" claim
        - Tier 3: reverse image search for provenance -> out-of-context check
        - Tier 4: manipulation/AI-gen detection        -> OUT OF SCOPE v1, force NEI
            |
            v
[2] Claim extraction + opinion tagging   (VeriScore-style; central claim flagged)
            |
            v
[3] Check-worthiness gate                (CheckThat-style filter; drops pure opinion)
            |
            v
[4] Iterative verification agent         (Papelo-style: one question at a time, one doc at a time)
        - Tavily search
        - Playwright + headless Chromium fetch for JS-heavy / login-walled URLs
            |
            v
[4.5] Evidence Reconciliation             <-- three-lens cascade + source-reliability lookup + consolidation
        - Lens 1: Text-Text (always)
        - Lens 2: Image-Text (only if modality includes image)
        - Lens 3: Cross-Modal (only if modality includes image)
        - Source-reliability four-step lookup runs in parallel
        - Consolidation: findings, source quality table, verdict label, presentation payload
            |
            v
[5] Verdict verification                  <-- CoVe-style audit; forces NEI on failure
            |
            v
[6] EVIDENCE FREEZE                       <-- writes immutable verdict object to store
            |
            v
[7] Tone renderer (1 of 3)               <-- reads presentation_payload + tone_neutral_justification ONLY
            |
            v
[8] X poster                             <-- posts reply, records post ID
            |
            v
[9] Outcome tracker                      <-- engagement, downstream behavior, debrief queue
```

Stages 1–6 are the **fixed backend**. Stage 7 is the **manipulation**. Stages 8–9 are **instrumentation**.

### 3.2 The invariance boundary

The frozen object written at Stage 6 is the contract between the backend and the experiment. Everything before Stage 6 must be deterministic with respect to a given target tweet and invocation timestamp. Everything after Stage 6 reads from the frozen object and is forbidden from issuing new searches or model calls that touch evidence.

The renderer at Stage 7 is restricted to two fields of the frozen object: `tone_neutral_justification` and `presentation_payload`. The reasoning fields (`cross_modal_report`, `consolidated_findings`, `source_quality_table`) are off-limits to the renderer by contract. A runtime check rejects any rendered output that references content outside the allowed fields.

## 4. Component Specifications

### 4.1 Ingestion (Stage 1)

- **Input:** target tweet ID, up to N parent/quoted tweets for thread context, attached image URLs and bytes.
- **Output:** normalized tweet text, author handle, timestamp, quoted/parent text, image bundle, `modality ∈ {text, image, mixed}` covariate.
- **Reference resolution matters.** A tweet saying "this is completely false" is uncheckable without the parent. Pull enough context to contextualize.

### 4.1.5 Multimodal extraction (Stage 1.5)

Runs only when images are present.

- **Tier 1 — text in image (ship).** VLM reads any text rendered in the image. Extracted text appended to the text path.
- **Tier 2 — depicted content (ship).** VLM produces a literal description, converted into a candidate proposition.
- **Tier 3 — provenance and out-of-context (ship).** Google Vision web detection finds earliest appearance and true context. Matched URLs, earliest-seen dates, captions become evidence records.
- **Tier 4 — manipulation / AI-generation detection (v1: forced NEI).** When the only available question is "was this image altered or AI-generated," return NotEnoughEvidence with an explicit note. Revisit in v2.

**Pilot-gated fallback:** if the Day 5 pilot fails the multimodal accuracy gate (AVerImaTeC veracity ≥ 0.40), v1 ships text-only and image-bearing tweets are skipped at ingestion with a "this tweet contains images, which v1 does not yet handle" reply.

**Freeze discipline:** snapshot all reverse-image-search results (URLs, captions, earliest-seen dates) at invocation time.

### 4.2 Claim extraction and opinion tagging (Stage 2)

- **Method:** prompt the reasoner to decompose the target text into atomic propositions, labeling each as `verifiable | opinion | mixed`. Mark exactly one claim as `is_central = true`.
- **Output:** list of `{text, type, source_span, is_central}` objects.
- **All-opinion behavior:** if no claim is `verifiable` or `mixed`, return `no_checkable_claim`. Skip Stages 4–6. Stage 7 produces a tone-appropriate "nothing to fact-check here" reply.

### 4.3 Check-worthiness gate (Stage 3)

- **Method:** CheckThat-style classifier (FactFinders pruning recipe). Filters trivial claims.
- **Output:** filtered claim list with check-worthiness scores.

### 4.4 Iterative verification agent (Stage 4)

Custom agent loop over Claude (frontier reasoning with native web search). One question at a time, one document at a time.

**Loop per claim:**
1. Generate a single verification question.
2. Search via Tavily.
3. Fetch the top retrieved document. JS-heavy domains (Facebook, Instagram, X, TikTok, LinkedIn, Quora, YouTube, archive sites) and login-walled URLs go through Playwright + headless Chromium; static domains use standard HTTP. Failed usefulness check (too short, dominated by nav/footer patterns) triggers Playwright retry.
4. Extract supporting/refuting snippets.
5. Decide whether another question is needed. If yes, generate the next conditioned on what was learned.

**Caps:** max 5 questions per claim, max 3 documents per question, 60-second wall-clock per claim.

**Output:** per claim, an evidence bundle of `{question, source_url, snippet, stance}` records. Stance is myopic; cross-source reasoning is deferred to Stage 4.5.

### 4.5 Evidence Reconciliation (Stage 4.5)

A single reasoning call operating on the full evidence bundle from Stage 4 plus all multimodal extraction outputs from Stage 1.5. Executes a three-lens cascade *plus* a parallel four-step source-reliability lookup, then consolidates.

#### 4.5.1 Three-lens cascade

- **Lens 1 — Text–Text reconciliation (always runs).** Reads all text evidence against the claim's text. For each atomic proposition in the central claim, classifies as `verified | refuted | disputed | unaddressed`; surfaces cross-source contradictions.
- **Lens 2 — Image–Text reconciliation (runs only when modality includes image).** Reads image evidence (OCR text, VLM depiction, reverse-image provenance) against the claim's text. Emits `image_caption_match ∈ {supports, contradicts, undetermined}`.
- **Lens 3 — Cross-Modal reconciliation (runs only when modality includes image).** Takes Lens 1 and Lens 2 outputs and explicitly hunts for cross-modal contradictions: image provenance refutes a text claim, or a text source discusses this exact image with a different caption.

#### 4.5.2 Source-reliability four-step lookup

Runs in parallel with the lens cascade, over every unique URL in the evidence bundle. First hit wins:

1. **IFCN signatory list →** `fact-checker` tier. Hard anchor.
2. **Wikipedia perennial sources list →** `reputable-news` / `low-quality` / `satirical` per the Wikipedia rating.
3. **Media Bias Fact Check (free tier) →** fills gaps Wikipedia doesn't cover.
4. **Model parametric prior →** fallback for anything not on the lists, with a forced meta-search ("what is [domain]?") if the model has no confident prior. Domains failing this step land in `unknown`.

The resulting source-quality table is passed into the consolidation prompt as a fixed input, not derived by the model. Every entry carries `tier_source ∈ {ifcn, wikipedia-rsp, mbfc, model-prior, meta-search}`.

#### 4.5.3 Consolidation

Lens outputs + source-quality table are folded into the frozen-object fields:

- `consolidated_findings` — verified / refuted / disputed / unaddressed propositions, each with sources.
- `source_quality_table` — populated by the four-step lookup, not by the model.
- `verdict_label` — emitted under the structural rule below.
- `tone_neutral_justification` — 1–3 sentences, source-anchored. The literal substrate the renderers wrap.
- `presentation_payload` — the renderer-facing substrate.

#### 4.5.4 Verdict label emission rule

- **Refuted** if `refuted_propositions` contains the central claim with ≥ 2 sources at tier `fact-checker` or `reputable-news`.
- **Supported** if `verified_propositions` contains the central claim with the same threshold.
- **Conflicting** if `disputed_propositions` contains the central claim and the source-quality table does not resolve the dispute to one side.
- **NotEnoughEvidence** otherwise, including the case where the only coverage is from `low-quality`, `unknown`, `satirical`, or single-source evidence.

Purely structural. Auditable from `consolidated_findings` and `source_quality_table` alone.

#### 4.5.5 Presentation payload

Closes the invariance loophole. Pre-commits:

- `headline_finding` — the single most important fact the bot should communicate.
- `counter_fact` — the correct version of the refuted claim when verdict is Refuted; null otherwise.
- `primary_sources_to_cite` — ordered list of `{url, display_name}`.
- `load_bearing_evidence_snippet` — one short quote the renderer may include verbatim.

### 4.6 Verdict verification (Stage 5)

CoVe-style audit. A second model call checks:

- `verdict_label` is consistent with `consolidated_findings` and `source_quality_table` under the Stage 4.5 structural rule.
- `presentation_payload.headline_finding` is the most important fact in the frozen object, not peripheral.
- `tone_neutral_justification` is faithful to `consolidated_findings` and cites only sources in `source_quality_table`.
- If evidence is thin (any required field empty or only low-tier for the central claim), verdict is `NotEnoughEvidence`.

**Any audit failure forces the verdict to `NotEnoughEvidence`** and triggers the no-correction path.

### 4.7 Evidence freeze (Stage 6)

Serializes the complete frozen object (Section 5.1) to immutable storage, keyed by `invocation_id`. Marks read-only. Pins:

- `model` (Claude, exact version string)
- `vlm_model` (Claude, exact version string)
- `search_provider` (Tavily, version)
- `reverse_image_provider` (Google Vision web detection, version)
- `fetch_layer` (Playwright, version)
- `source_reliability_lists_version` (IFCN list date, Wikipedia RSP commit, MBFC snapshot date)
- `pipeline_commit` (git SHA)

Stores raw retrieved documents alongside the verdict for full audit reconstruction.

### 4.8 Tone renderer (Stage 7)

**Input:** `presentation_payload` and `tone_neutral_justification`. Nothing else. Prompt explicitly forbids reading reasoning fields; runtime check enforces.

**Three renderers, three system prompts, one shared payload:**

- **Agreeable / empathetic.** Affirms the person, then supplies the factual alternative. Lewandowsky Debunking Handbook structure. Register: "Totally get why this is confusing — here's what the evidence actually shows…"
- **Neutral.** Plain correction with source. Bode, Vraga & Tully (2020) register: "This is not accurate. According to [source]…"
- **Agonistic / satirical.** Mockery or pointed challenge wrapped around the same correction. Boukes & Hameleers (2022) register: pointed, ridiculing, but factually identical.

**Hard constraints (applied to all renderers, agonistic-specific noted):**

- The factual content (headline finding, counter-fact), the verdict implied, and the cited sources are identical across renderers. Only wording, stylistic ordering, and affect change.
- No facts may be introduced outside `presentation_payload`.
- At least one source link from `primary_sources_to_cite` must appear.
- **Agonistic boundary:** targets the *claim* and the *source*, not the person. No profanity, no slurs, no demographic or appearance-based mockery, no attacks on identity. Register ceiling is pointed rhetorical questions, exaggeration, and sarcasm directed at the claim's content or the source's credibility.

**Output:** final reply text under the X character limit, with at least one source link.

### 4.9 X poster (Stage 8)

Posts the rendered text as a reply to the target tweet from the disclosed research bot account. Records `posted_tweet_id`, timestamp, participant + condition assignment.

### 4.10 Outcome tracker (Stage 9)

Collects engagement on the bot's correction; engagement on the target user's subsequent posts (Mosleh downstream-sharing-quality outcome); survey responses from recruited participants. Feeds the debrief queue (Section 9).

## 5. Data Model

### 5.1 Frozen verdict object (the invariance contract)

```json
{
  "invocation_id": "uuid",
  "target_tweet_id": "string",
  "invocation_time": "ISO-8601",
  "thread_context": "string",
  "modality": "text | image | mixed",
  "backend_version": {
    "model": "string",
    "vlm_model": "string",
    "search_provider": "string",
    "reverse_image_provider": "string",
    "fetch_layer": "string",
    "source_reliability_lists_version": {
      "ifcn": "ISO-8601",
      "wikipedia_rsp": "git-sha",
      "mbfc": "ISO-8601"
    },
    "pipeline_commit": "git-sha"
  },
  "attached_images": [
    {
      "image_id": "string",
      "image_url": "string",
      "ocr_text": "string",
      "vlm_description": "string",
      "provenance": [
        {
          "match_url": "string",
          "earliest_seen": "ISO-8601 | null",
          "match_caption": "string"
        }
      ],
      "manipulation_check": "out_of_scope_nei"
    }
  ],
  "claims": [
    {
      "claim_id": "string",
      "text": "string",
      "type": "verifiable | opinion | mixed",
      "modality": "text | image | mixed",
      "check_worthy": true,
      "is_central": true,
      "evidence": [
        {
          "question": "string",
          "source_url": "string",
          "snippet": "string",
          "stance": "supports | refutes | neutral"
        }
      ]
    }
  ],
  "cross_modal_report": {
    "lens_1_text_text": {
      "narrative": "string",
      "cross_source_contradictions": [
        {
          "topic": "string",
          "sources_for": [{"url": "string", "tier": "string"}],
          "sources_against": [{"url": "string", "tier": "string"}],
          "resolution": "string"
        }
      ]
    },
    "lens_2_image_text": {
      "ran": true,
      "narrative": "string",
      "image_provenance": {
        "earliest_seen": "ISO-8601 | unknown",
        "true_caption": "string | unknown",
        "true_context": "string | unknown",
        "provenance_sources": ["string"]
      },
      "image_caption_match": "supports | contradicts | undetermined"
    },
    "lens_3_cross_modal": {
      "ran": true,
      "narrative": "string",
      "modality_conflicts": [
        {
          "description": "string",
          "text_path_says": "string",
          "image_path_says": "string",
          "weight_of_evidence_favors": "text | image | undetermined"
        }
      ]
    }
  },
  "consolidated_findings": {
    "verified_propositions": [
      {
        "proposition": "string",
        "supporting_sources": [{"url": "string", "tier": "string"}],
        "is_central": true
      }
    ],
    "refuted_propositions": [
      {
        "proposition": "string",
        "refuting_sources": [{"url": "string", "tier": "string"}],
        "counter_fact": "string",
        "is_central": true
      }
    ],
    "disputed_propositions": [
      {
        "proposition": "string",
        "sources_for": [{"url": "string", "tier": "string"}],
        "sources_against": [{"url": "string", "tier": "string"}],
        "weight_of_evidence_favors": "for | against | undetermined",
        "is_central": true
      }
    ],
    "unaddressed_propositions": [
      {
        "proposition": "string",
        "reason": "no evidence retrieved | evidence retrieved but silent",
        "is_central": true
      }
    ]
  },
  "source_quality_table": [
    {
      "url": "string",
      "tier": "fact-checker | reputable-news | primary-source | aggregator | low-quality | satirical | unknown",
      "tier_source": "ifcn | wikipedia-rsp | mbfc | model-prior | meta-search",
      "rationale": "string"
    }
  ],
  "verdict_label": "Supported | Refuted | NotEnoughEvidence | Conflicting",
  "tone_neutral_justification": "string (1-3 sentences, source-anchored)",
  "presentation_payload": {
    "headline_finding": "string",
    "counter_fact": "string | null",
    "primary_sources_to_cite": [{"url": "string", "display_name": "string"}],
    "load_bearing_evidence_snippet": "string"
  },
  "overall_state": "checked | no_checkable_claim",
  "frozen": true
}
```

### 5.2 Rendered reply record

```json
{
  "invocation_id": "uuid",
  "participant_id": "string",
  "condition": "agreeable | neutral | agonistic",
  "rendered_text": "string",
  "posted_tweet_id": "string",
  "posted_at": "ISO-8601"
}
```

### 5.3 Outcome record

```json
{
  "posted_tweet_id": "string",
  "likes": 0,
  "reposts": 0,
  "quote_tweets": 0,
  "replies": 0,
  "target_user_subsequent_sharing_quality": "float | null",
  "claim_modality": "text | image | mixed",
  "participant_survey": { "belief_pre": 0, "belief_post": 0, "perceived_hostility": 0 },
  "collected_at": "ISO-8601"
}
```

## 6. Technology Stack (locked)

| Layer | Choice |
|---|---|
| Orchestration | Custom agent loop (~200 LOC). No Loki. |
| Reasoner | Claude (frontier reasoning with native web search). Swappable but pinned for the study. |
| VLM | Same Claude model family for Tiers 1–2 multimodal extraction. |
| Reverse image search | Google Vision web detection. |
| Headless browser fetch | Playwright + headless Chromium. |
| Claim extraction | VeriScore-style verifiable-claim extractor (prompt-based on Claude). |
| Check-worthiness | CheckThat-style filter (FactFinders pruning recipe). |
| Search | Tavily. |
| Source-reliability anchors | IFCN signatory list + Wikipedia perennial sources + MBFC free tier. |
| Verdict audit | Stage 5 CoVe-style verification pass. |
| Storage | Append-only object store for frozen objects + relational store for outcomes. |
| Platform | X API Basic ($200/month, 100 posts/day). |

## 7. Build-Week Plan

1. **Day 1.** Set up X API Basic, disclosed bot account, append-only object store. Stand up custom agent loop with Claude + Tavily + Playwright fetch layer. Smoke-test end-to-end on three hand-picked claims.
2. **Day 2.** Implement Stages 2 (claim extraction), 3 (check-worthiness), 4 (iterative verification). Validate on the AVeriTeC dev set. Require score ≥ 0.45 before proceeding.
3. **Day 3 morning.** Implement Stage 1.5 (Tiers 1–3 multimodal), Stage 4.5 (Evidence Reconciliation with three-lens cascade and source-reliability four-step lookup). Validate multimodal path on a small AVerImaTeC dev subset.
4. **Day 3 afternoon.** Implement Stage 5 audit and Stage 6 freeze. Add runtime check that Stage 7 cannot read reasoning fields.
5. **Day 4 morning.** Write three tone renderer prompts. Test on five frozen objects to confirm factual invariance via automated diff.
6. **Day 4 afternoon.** Wire Stage 8 X poster, Stage 9 outcome tracker, opt-out registry, debrief queue infrastructure.
7. **Day 5 morning.** Pilot on ~30 sham tweets (mix of text-only and image-bearing, plus a few all-opinion to verify the no-checkable-claim path).
8. **Day 5 afternoon.** Manipulation check (raters classify tone), invariance check (raters rate factual equivalence), accuracy spot-check. Decide go / no-go for recruited participants. If multimodal accuracy gate fails, ship text-only and skip image-bearing tweets at ingestion.

## 8. Validation and Accuracy Gates

- **Backend accuracy gate.** AVeriTeC dev score ≥ 0.45 before any live use.
- **Multimodal accuracy gate.** AVerImaTeC dev veracity ≥ 0.40 before any image-bearing tweet is fact-checked live. Failure → ship text-only.
- **Invariance gate.** For a fixed `invocation_id`, the three rendered outputs are set-equivalent over `presentation_payload`: same headline finding, same sources in the same order, same counter-fact. Verified by automated diff plus human rating in pilot.
- **Verdict audit gate.** Stage 5 must agree with Stage 4.5 under the structural rule. Disagreement forces NEI.
- **Manipulation check.** Pilot raters classify tone correctly above chance and rate factual content statistically equivalent across conditions.
- **NEI calibration.** Spot-check that thin-evidence cases return NEI.

## 9. Ethics, IRB, and Safety Controls

The recruited participant who invokes the bot is consented. The harder subjects are the **target user** whose tweet is publicly corrected and the **bystanders** who see the reply.

- **Disclosure.** The bot account clearly identifies as a fact-checking research bot.
- **Opt-out registry.** Target users can block or opt out; the bot honors a do-not-reply list.
- **Post-hoc debriefing.** Identifiable target users enter a debrief queue. Reuter et al. (2024) — IRB approval alone is not sufficient.
- **Deletion on request.** Any corrected user can request removal of the reply and their data.
- **Preregistration.** Primary outcome (recruited-participant belief update), secondary outcomes (engagement, perceived hostility, downstream sharing quality), directional hypotheses, registered on OSF before launch.
- **Stopping rule for the agonistic arm.** Predefined threshold (20% increase in downstream low-quality sharing in the agonistic arm) halts that arm. Mosleh et al. (2021).
- **Satire boundary.** Renderer-prompt constraint: targets claim/source, not person; no profanity, slurs, demographic or appearance-based attacks.
- **Consent expectations.** Address CITI guidance (4 of 5 posters expect to be asked) directly in the IRB application.

## 10. Failure Modes and Mitigations

| Failure mode | Mitigation |
|---|---|
| Backend fact-checks an opinion as if it were a fact | Opinion tagging (4.2) + check-worthiness gate (4.3); `no_checkable_claim` state |
| Live re-retrieval drifts evidence across conditions | Evidence freeze (4.7); renderer forbidden from retrieval |
| Confident wrong verdict (Grok-style) | Structural verdict rule (≥ 2 reliable-tier sources); Stage 5 audit forces NEI on failure |
| Tone manipulation shifts factual content | Renderer reads only `presentation_payload + tone_neutral_justification`; runtime check; pilot manipulation check |
| Renderer drifts which finding is headlined across conditions | Pre-committed `headline_finding` in `presentation_payload` |
| Agonistic arm causes real-world harm | Stopping rule; opt-out; debriefing; satire boundary in renderer prompt |
| Platform policy or pricing changes mid-study | Bot kept portable to Bluesky or Mastodon |
| Image used out of context (real photo, false caption) | Tier 3 reverse image search + Lens 3 cross-modal reconciliation |
| Confident "this image is fake" verdict | Tier 4 forced to NEI in v1 |
| Reverse image search drifts across conditions | Snapshot matched URLs, captions, dates at freeze |
| JS-heavy / login-walled source returns junk content | Playwright fetch layer |
| Satirical source supplies "evidence" for false claim | Source quality table demotes `satirical` and `low-quality`; verdict rule requires ≥ 2 reliable-tier sources |
| Source-reliability anchors miss a key domain | Four-step lookup falls through to model prior + meta-search; `tier_source = model-prior` flagged for post-hoc audit |
| Multimodal accuracy fails pilot gate | Ship text-only; skip image-bearing tweets with explanatory reply |
| Overstated effect expectations | A single reply will not approach Costello's 20% reduction (8-minute dialogue). Set upper bound accordingly. |

## 11. Locked Decisions (resolves v0.3 Section 11)

| Decision | Locked value |
|---|---|
| Number of claims per reply | Headline only (one `is_central` claim per tweet) |
| Satire boundary | Targets claim and source, not person; no profanity, slurs, demographic or appearance-based attacks |
| Primary outcome population | Recruited participant only in v1; bystander analysis v2 |
| Backend pipeline | Custom agent loop; no Loki bake-off |
| Multimodal scope | Tiers 1–3 ship; Tier 4 NEI; pilot-gated fallback to text-only |
| Source-reliability mechanism | Four-step lookup (IFCN → Wikipedia RSP → MBFC → model prior + meta-search) |
| Verdict rule threshold | ≥ 2 sources at `fact-checker` or `reputable-news` tier on central claim |
| Stage 4.5 architecture | One reasoning call with internal three-lens cascade |
| Reasoner | Claude (frontier reasoning + native web search) |
| Search provider | Tavily |
| Reverse image search | Google Vision web detection |
| Headless fetch layer | Playwright + headless Chromium |
| Veracity score | None. Verdict label only. |

---

## Appendix A: Worked example — Rosa Camfield

**Claim (target tweet):** "Photo shows a 101-year-old woman who has given birth to her 17th child." Attached image: an elderly woman holding a newborn.

### Stage 4.5 emits (excerpt):

```json
{
  "consolidated_findings": {
    "verified_propositions": [
      {"proposition": "The photograph is authentic and was taken in March 2015.",
       "supporting_sources": [{"url": "thequint.com/...", "tier": "fact-checker"},
                              {"url": "vishvasnews.com/...", "tier": "fact-checker"}],
       "is_central": false},
      {"proposition": "The woman in the photograph is Rosa Camfield.",
       "supporting_sources": [{"url": "snopes.com/...", "tier": "fact-checker"},
                              {"url": "thequint.com/...", "tier": "fact-checker"}],
       "is_central": false}
    ],
    "refuted_propositions": [
      {"proposition": "The woman in the photograph gave birth to her 17th child at age 101.",
       "refuting_sources": [{"url": "snopes.com/...", "tier": "fact-checker"},
                            {"url": "thequint.com/...", "tier": "fact-checker"},
                            {"url": "africacheck.org/...", "tier": "fact-checker"}],
       "counter_fact": "The baby is the woman's great-granddaughter, not her child. The 101-year-old mother story originated from a satirical website.",
       "is_central": true}
    ]
  },
  "source_quality_table": [
    {"url": "snopes.com/...", "tier": "fact-checker", "tier_source": "ifcn", "rationale": "IFCN signatory."},
    {"url": "thequint.com/...", "tier": "fact-checker", "tier_source": "ifcn", "rationale": "IFCN signatory."},
    {"url": "africacheck.org/...", "tier": "fact-checker", "tier_source": "ifcn", "rationale": "IFCN signatory."},
    {"url": "vishvasnews.com/...", "tier": "fact-checker", "tier_source": "ifcn", "rationale": "IFCN signatory."},
    {"url": "worldrecordacademy.org/...", "tier": "low-quality", "tier_source": "wikipedia-rsp", "rationale": "Wikipedia perennial sources: aggregator with no editorial oversight."},
    {"url": "worldnewsdailyreport.com/...", "tier": "satirical", "tier_source": "wikipedia-rsp", "rationale": "Wikipedia perennial sources: self-described satirical/fictional news site."}
  ],
  "verdict_label": "Refuted",
  "tone_neutral_justification": "The photograph is real but miscaptioned. It shows 101-year-old Rosa Camfield holding her two-week-old great-granddaughter Kaylee in March 2015 — not her own child. The 'mother of 17 at 101' story originated from a self-described satirical news site and has been debunked by multiple fact-checkers including Snopes and The Quint.",
  "presentation_payload": {
    "headline_finding": "The photo is real, but it shows a great-grandmother with her great-granddaughter, not a mother with her newborn.",
    "counter_fact": "The woman is Rosa Camfield, 101, and the baby is her great-granddaughter Kaylee. The photo is from March 2015.",
    "primary_sources_to_cite": [
      {"url": "snopes.com/...", "display_name": "Snopes"},
      {"url": "thequint.com/...", "display_name": "The Quint"}
    ],
    "load_bearing_evidence_snippet": "Rosa Camfield, 101, holding her great-granddaughter Kaylee — not her own child. (The Quint, 2016)"
  }
}
```

### Stage 7 renders (three conditions, same payload):

**Agreeable:** "It's easy to be moved by this — but the photo is actually from a really sweet moment in 2015: 101-year-old Rosa Camfield meeting her great-granddaughter Kaylee for the first time. The 'mother of 17 at 101' story came from a satirical site and was debunked by Snopes and The Quint."

**Neutral:** "This image is miscaptioned. It shows 101-year-old Rosa Camfield with her great-granddaughter Kaylee (March 2015), not a mother with her newborn. The story originated from a satirical site and was debunked by Snopes and The Quint."

**Agonistic:** "Ten seconds with reverse image search: she's a great-grandmother, not a mother. The 'mother of 17 at 101' story came from a satire site that says it makes things up. Snopes and The Quint covered this years ago."

All three reference the same facts, cite the same sources in the same order, and state the same counter-fact. Only register varies. The invariance contract holds because the renderer never saw `cross_modal_report`, `consolidated_findings`, or `source_quality_table` — only `presentation_payload` and `tone_neutral_justification`. The agonistic rendering mocks the claim and the satirical source, not the target user.