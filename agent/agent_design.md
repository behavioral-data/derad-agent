# Agonistic Fact-Checking Bot — System Design Specification

**Version:** 0.5 (build-week final; Azure stack locked)
**Owner:** Advait
**Status:** Ready for implementation
**Purpose:** Define a fixed verification backend and a swappable tone-rendering layer for a between-subjects field study on X, in which recruited participants invoke a fact-checking bot to reply to real tweets under one of three tone conditions (agreeable, neutral, satirical).

**Changes from v0.4:**

- **Stack moved fully to Azure.** Claude Sonnet via Azure AI Foundry (reasoner + VLM); Bing Grounding via Azure AI Foundry (web search); home-built reverse image search on Azure AI Search + multimodal embeddings (Tier 3); Azure Blob Storage + Cosmos DB (frozen object store + outcomes); Azure Container Apps (compute). Playwright + headless Chromium retained for the fetch layer (vendor-neutral).
- **Tier 3 reverse image search is now home-built** as a two-track hybrid: Track A (description-grounded search via Bing Grounding) ships in v1; Track B (image-similarity vector index over a fact-checker image corpus) is a Day-3 stretch.
- **New accuracy gate for the home-built reverse image search.** If Track A fails to recover provenance on a held-out set of known out-of-context cases at parity with Google Vision–level recall, v1 ships text-only.
- All other decisions from v0.4 unchanged.

---

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
- Bystander belief tracking in v1.

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
        - Tier 1: read text in image (Claude VLM via Azure AI Foundry)
        - Tier 2: describe depicted content (Claude VLM via Azure AI Foundry)
        - Tier 3: home-built reverse image search
                  Track A (ships v1): VLM description -> Bing Grounding -> articles about the image
                  Track B (stretch):  Azure AI Search vector index over fact-checker image corpus
        - Tier 4: manipulation / AI-gen detection -> OUT OF SCOPE v1, force NEI
            |
            v
[2] Claim extraction + opinion tagging   (VeriScore-style; central claim flagged)
            |
            v
[3] Check-worthiness gate                (CheckThat-style filter; drops pure opinion)
            |
            v
[4] Iterative verification agent         (Papelo-style: one question at a time, one doc at a time)
        - Bing Grounding (Azure AI Foundry) for web search
        - Playwright + headless Chromium for JS-heavy / login-walled URLs
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
[6] EVIDENCE FREEZE                       <-- Azure Blob (artifacts) + Cosmos DB (index)
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

### 4.1.5 Multimodal extraction (Stage 1.5)

Runs only when images are present.

- **Tier 1 — text in image (ship).** Claude VLM via Azure AI Foundry reads any text rendered in the image. Extracted text appended to the text path.
- **Tier 2 — depicted content (ship).** Claude VLM produces a literal description of subjects, environment, distinctive visual features.
- **Tier 3 — reverse image search (home-built, two tracks).** See 4.1.5.1 below.
- **Tier 4 — manipulation / AI-generation detection (v1: forced NEI).** Edit-detection and diffusion detectors generalize poorly; C2PA content credentials are sparse. Return NotEnoughEvidence with an explicit note. Revisit in v2.

**Pilot-gated fallback:** if the Day 5 pilot fails the multimodal accuracy gate (Section 8), v1 ships text-only and image-bearing tweets are skipped at ingestion with an explanatory reply.

**Freeze discipline:** snapshot all Tier 3 results (matched URLs, captions, earliest-seen dates, match confidence) at invocation time.

#### 4.1.5.1 Tier 3: home-built reverse image search

Azure does not currently offer a production reverse-image-web-search product (Bing Visual Search was retired). We implement Tier 3 ourselves as a two-track hybrid.

**Track A — description-grounded search (ships v1).** Pure text-search workaround for image-provenance lookup. Cheap, fast, and catches the canonical out-of-context case where the image has been *written about* in the indexed web.

1. Claude VLM produces a detailed visual description of the image (subjects, environment, clothing, distinctive features, any visible text, approximate setting).
2. Claude extracts named entities and distinctive descriptors from the description (e.g., "elderly woman," "newborn baby," "blue blanket," "domestic interior").
3. Bing Grounding (Azure AI Foundry) is queried with the description plus entities, with a fact-checker domain bias (`site:snopes.com OR site:thequint.com OR site:africacheck.org OR ...`) and a fresh general-web query.
4. Returned articles are fetched (Playwright when needed) and their text is screened by Claude for references to *this specific image* — distinctive descriptors must match. Matches become provenance evidence records.

Limitation: this catches images that have been discussed in indexed articles. It does **not** catch images that have only been visually copied without text discussion, nor images that have been visually altered.

**Track B — vector image-similarity search (Day-3 stretch).** Direct image-to-image lookup for cases where Track A returns nothing.

1. Pre-build a vector index of fact-checker-published images. Sources: Snopes, The Quint, AFP Fact Check, Reuters Fact Check, Africa Check archives (publicly accessible image URLs from their debunk articles). Index using Azure AI Vision Image Embeddings (Florence) or Cohere multimodal embeddings via Azure AI Foundry, stored in Azure AI Search vector index.
2. At invocation, embed the claim image and query the index for top-k similar images.
3. For each high-similarity match, retrieve the originating fact-checker article URL and treat as a provenance evidence record.

Track B ships only if Day 3 has spare capacity; otherwise it's v2.

### 4.2 Claim extraction and opinion tagging (Stage 2)

- **Method:** prompt Claude Sonnet (via Azure AI Foundry) to decompose the target text into atomic propositions, labeling each as `verifiable | opinion | mixed`. Mark exactly one claim as `is_central = true`.
- **Output:** list of `{text, type, source_span, is_central}` objects.
- **All-opinion behavior:** if no claim is `verifiable` or `mixed`, return `no_checkable_claim`. Skip Stages 4–6.

### 4.3 Check-worthiness gate (Stage 3)

- **Method:** CheckThat-style filter (FactFinders pruning recipe), prompt-based on Claude.

### 4.4 Iterative verification agent (Stage 4)

Custom agent loop over Claude Sonnet (Azure AI Foundry). One question at a time, one document at a time.

**Loop per claim:**
1. Generate a single verification question.
2. Search via Bing Grounding (Azure AI Foundry).
3. Fetch the top retrieved document. JS-heavy domains (Facebook, Instagram, X, TikTok, LinkedIn, Quora, YouTube, archive sites) and login-walled URLs go through Playwright + headless Chromium; static domains use Bing Grounding's returned content directly. Failed usefulness check (too short, dominated by nav/footer patterns) triggers Playwright retry.
4. Extract supporting/refuting snippets.
5. Decide whether another question is needed. If yes, generate the next conditioned on what was learned.

**Caps:** max 5 questions per claim, max 3 documents per question, 60-second wall-clock per claim.

**Output:** per claim, an evidence bundle of `{question, source_url, snippet, stance}` records.

### 4.5 Evidence Reconciliation (Stage 4.5)

A single Claude reasoning call (via Azure AI Foundry) operating on the full evidence bundle from Stage 4 plus all Stage 1.5 outputs. Executes a three-lens cascade *plus* a parallel four-step source-reliability lookup, then consolidates.

#### 4.5.1 Three-lens cascade

- **Lens 1 — Text–Text reconciliation (always runs).** Reads all text evidence against the claim's text. For each atomic proposition in the central claim, classifies as `verified | refuted | disputed | unaddressed`; surfaces cross-source contradictions.
- **Lens 2 — Image–Text reconciliation (runs only when modality includes image).** Reads image evidence (OCR text, VLM depiction, Tier 3 provenance from Track A and Track B) against the claim's text. Emits `image_caption_match ∈ {supports, contradicts, undetermined}`.
- **Lens 3 — Cross-Modal reconciliation (runs only when modality includes image).** Takes Lens 1 and Lens 2 outputs and explicitly hunts for cross-modal contradictions: image provenance refutes a text claim, or a text source discusses this exact image with a different caption.

#### 4.5.2 Source-reliability four-step lookup

Runs in parallel with the lens cascade, over every unique URL in the evidence bundle. First hit wins:

1. **IFCN signatory list →** `fact-checker` tier. Hard anchor.
2. **Wikipedia perennial sources list →** `reputable-news` / `low-quality` / `satirical` per the Wikipedia rating.
3. **Media Bias Fact Check (free tier) →** fills gaps Wikipedia doesn't cover.
4. **Model parametric prior →** fallback for anything not on the lists, with a forced meta-search via Bing Grounding ("what is [domain]?") if Claude has no confident prior. Domains failing this step land in `unknown`.

Every entry carries `tier_source ∈ {ifcn, wikipedia-rsp, mbfc, model-prior, meta-search}`. The resulting source-quality table is passed into the consolidation prompt as a fixed input, not derived by the model.

#### 4.5.3 Consolidation

Lens outputs + source-quality table are folded into the frozen-object fields:

- `consolidated_findings` — verified / refuted / disputed / unaddressed propositions.
- `source_quality_table` — populated by the four-step lookup.
- `verdict_label` — emitted under the structural rule below.
- `tone_neutral_justification` — 1–3 sentences, source-anchored.
- `presentation_payload` — the renderer-facing substrate.

#### 4.5.4 Verdict label emission rule

- **Refuted** if `refuted_propositions` contains the central claim with ≥ 2 sources at tier `fact-checker` or `reputable-news`.
- **Supported** if `verified_propositions` contains the central claim with the same threshold.
- **Conflicting** if `disputed_propositions` contains the central claim and the source-quality table does not resolve the dispute to one side.
- **NotEnoughEvidence** otherwise.

#### 4.5.5 Presentation payload

Pre-commits:

- `headline_finding` — the single most important fact the bot should communicate.
- `counter_fact` — the correct version of the refuted claim when verdict is Refuted; null otherwise.
- `primary_sources_to_cite` — ordered list of `{url, display_name}`.
- `load_bearing_evidence_snippet` — one short quote the renderer may include verbatim.

### 4.6 Verdict verification (Stage 5)

CoVe-style audit. A second Claude call checks:

- `verdict_label` is consistent with `consolidated_findings` and `source_quality_table` under the Stage 4.5 structural rule.
- `presentation_payload.headline_finding` is the most important fact in the frozen object.
- `tone_neutral_justification` is faithful to `consolidated_findings` and cites only sources in `source_quality_table`.
- If evidence is thin, verdict is `NotEnoughEvidence`.

Any audit failure forces verdict to `NotEnoughEvidence` and the no-correction path.

### 4.7 Evidence freeze (Stage 6)

Serializes the complete frozen object (Section 5.1) to **Azure Blob Storage** (raw JSON, append-only container with immutability policy), with an index entry in **Azure Cosmos DB** keyed by `invocation_id`. Marks read-only. Pins exact versions for every layer.

### 4.8 Tone renderer (Stage 7)

**Input:** `presentation_payload` and `tone_neutral_justification`. Nothing else.

**Three renderers, three system prompts, one shared payload:**

- **Agreeable / empathetic.** Lewandowsky Debunking Handbook structure.
- **Neutral.** Bode, Vraga & Tully (2020) register.
- **Satirical.** Boukes & Hameleers (2022) register.

**Hard constraints:**

- Factual content, verdict implied, and cited sources identical across renderers. Only wording, stylistic ordering, and affect change.
- No facts introduced outside `presentation_payload`.
- At least one source link from `primary_sources_to_cite`.
- **Satirical boundary:** targets the *claim* and the *source*, not the person. No profanity, no slurs, no demographic or appearance-based mockery, no attacks on identity.

### 4.9 X poster (Stage 8)

Posts the rendered text as a reply to the target tweet from the disclosed research bot account. Records `posted_tweet_id`, timestamp, participant + condition assignment.

### 4.10 Outcome tracker (Stage 9)

Collects engagement on the bot's correction; engagement on the target user's subsequent posts; survey responses from recruited participants. Feeds the debrief queue.

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
    "model": "claude-sonnet-via-azure-foundry@<version>",
    "vlm_model": "claude-sonnet-via-azure-foundry@<version>",
    "search_provider": "bing-grounding-azure-foundry@<version>",
    "reverse_image_search": {
      "track_a": "description-grounded@<commit>",
      "track_b": "azure-ai-search-vector@<index-version> | not-deployed"
    },
    "fetch_layer": "playwright@<version>",
    "source_reliability_lists_version": {
      "ifcn": "ISO-8601",
      "wikipedia_rsp": "git-sha",
      "mbfc": "ISO-8601"
    },
    "storage": "azure-blob@<container> + cosmos-db@<container>",
    "pipeline_commit": "git-sha"
  },
  "attached_images": [
    {
      "image_id": "string",
      "image_url": "string",
      "ocr_text": "string",
      "vlm_description": "string",
      "vlm_entities": ["string"],
      "provenance": [
        {
          "match_url": "string",
          "match_source_track": "a | b",
          "earliest_seen": "ISO-8601 | null",
          "match_caption": "string",
          "match_confidence": "float | null"
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
    "lens_1_text_text": { "narrative": "string", "cross_source_contradictions": [] },
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
      "modality_conflicts": []
    }
  },
  "consolidated_findings": {
    "verified_propositions": [],
    "refuted_propositions": [],
    "disputed_propositions": [],
    "unaddressed_propositions": []
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
  "condition": "agreeable | neutral | satirical",
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

## 6. Technology Stack (locked, Azure-native)

| Layer | Choice |
|---|---|
| Reasoner | Claude Sonnet via Azure AI Foundry |
| VLM | Claude Sonnet via Azure AI Foundry (same model, multimodal mode) |
| Web search | Bing Grounding (Azure AI Foundry) |
| Reverse image search | Home-built. Track A: description-grounded via Bing Grounding. Track B (stretch): vector index on Azure AI Search using Azure AI Vision Image Embeddings (Florence) or Cohere multimodal embeddings via Azure AI Foundry. |
| Fetch layer | Playwright + headless Chromium (vendor-neutral, deployed inside Azure Container Apps) |
| Orchestration | Custom agent loop (~200 LOC) running on Azure Container Apps |
| Claim extraction | Prompt-based on Claude |
| Check-worthiness | Prompt-based on Claude (FactFinders recipe) |
| Source-reliability anchors | IFCN signatory list + Wikipedia perennial sources + MBFC free tier |
| Storage (frozen objects) | Azure Blob Storage with immutability policy (append-only) |
| Storage (outcomes, index) | Azure Cosmos DB |
| Secret management | Azure Key Vault |
| Monitoring | Azure Monitor + Application Insights |
| Platform | X API Basic ($200/month, 100 posts/day) |

## 7. Build-Week Plan

1. **Day 1.** Provision Azure resources (Foundry project, Cosmos DB account, Blob container with immutability policy, Container App, Key Vault). Wire Claude Sonnet + Bing Grounding through Azure AI Foundry. Set up X API Basic + disclosed bot account. Smoke-test end-to-end on three hand-picked claims.
2. **Day 2.** Implement Stages 2 (claim extraction), 3 (check-worthiness), 4 (iterative verification with Bing Grounding + Playwright fallback). Validate on the AVeriTeC dev set. Require score ≥ 0.45 before proceeding.
3. **Day 3 morning.** Implement Stage 1.5 Tier 1–2 multimodal extraction (Claude VLM). Implement Tier 3 Track A (description-grounded reverse image search via Bing Grounding). Implement source-reliability four-step lookup. Implement Stage 4.5 Evidence Reconciliation.
4. **Day 3 afternoon.** Implement Stage 5 audit and Stage 6 freeze (Blob + Cosmos). Runtime check that Stage 7 cannot read reasoning fields. **If time:** start Tier 3 Track B (vector index build over fact-checker image archives).
5. **Day 4 morning.** Three tone renderer prompts. Test on five frozen objects to confirm factual invariance via automated diff.
6. **Day 4 afternoon.** Wire Stage 8 X poster, Stage 9 outcome tracker, opt-out registry, debrief queue.
7. **Day 5 morning.** Pilot on ~30 sham tweets (text-only + image-bearing + all-opinion).
8. **Day 5 afternoon.** Manipulation check, invariance check, accuracy spot-check. Decide go / no-go. If multimodal gate or reverse-image-search gate fails → ship text-only fallback.

## 8. Validation and Accuracy Gates

- **Backend accuracy gate.** AVeriTeC dev score ≥ 0.45 before any live use.
- **Multimodal accuracy gate.** AVerImaTeC dev veracity ≥ 0.40 before any image-bearing tweet is fact-checked live.
- **Home-built reverse image search gate.** Curate a held-out set of ~20 known out-of-context image cases (real photo + false caption, where the true context has been documented by a fact-checker). Track A must recover the correct provenance article for ≥ 60% of cases. Below that → ship text-only and skip image-bearing tweets.
- **Invariance gate.** For a fixed `invocation_id`, the three rendered outputs are set-equivalent over `presentation_payload`.
- **Verdict audit gate.** Stage 5 must agree with Stage 4.5 under the structural rule. Disagreement forces NEI.
- **Manipulation check.** Pilot raters classify tone correctly above chance and rate factual content statistically equivalent across conditions.
- **NEI calibration.** Spot-check thin-evidence cases return NEI.

## 9. Ethics, IRB, and Safety Controls

- **Disclosure.** Bot account clearly identifies as a fact-checking research bot.
- **Opt-out registry.** Honored against do-not-reply list.
- **Post-hoc debriefing.** Identifiable target users enter a debrief queue. Reuter et al. (2024).
- **Deletion on request.** Any corrected user can request removal.
- **Preregistration.** OSF registration before launch.
- **Stopping rule for the satirical arm.** 20% increase in downstream low-quality sharing halts that arm.
- **Satire boundary.** Renderer-prompt constraint: targets claim/source, not person.
- **Consent expectations.** CITI guidance addressed in IRB application.
- **Data residency.** Azure region pinned (suggested: East US 2) for predictable jurisdiction. Document in IRB.

## 10. Failure Modes and Mitigations

| Failure mode | Mitigation |
|---|---|
| Backend fact-checks an opinion as if it were a fact | Opinion tagging + check-worthiness gate; `no_checkable_claim` state |
| Live re-retrieval drifts evidence across conditions | Evidence freeze; renderer forbidden from retrieval |
| Confident wrong verdict | Structural verdict rule (≥ 2 reliable-tier sources); Stage 5 audit forces NEI |
| Tone manipulation shifts factual content | Renderer reads only `presentation_payload + tone_neutral_justification`; runtime check |
| Renderer drifts headline across conditions | Pre-committed `headline_finding` in `presentation_payload` |
| Satirical arm causes real-world harm | Stopping rule; opt-out; debriefing; satire boundary in renderer prompt |
| Platform policy or pricing changes | Bot kept portable to Bluesky or Mastodon |
| Image used out of context | Tier 3 Track A (description-grounded search) + Lens 3 cross-modal reconciliation |
| Confident "this image is fake" verdict | Tier 4 forced to NEI |
| Reverse image search drifts across conditions | Snapshot matched URLs, captions, dates at freeze |
| JS-heavy / login-walled source returns junk | Playwright fetch layer |
| Satirical source supplies "evidence" | Source quality table demotes `satirical`/`low-quality`; verdict rule requires ≥ 2 reliable-tier sources |
| Source-reliability anchors miss a key domain | Four-step lookup falls through to model prior + meta-search; `tier_source = model-prior` flagged for audit |
| **Home-built reverse image search underperforms Google Vision baseline** | **Track B vector index as backup; pilot-gated fallback to text-only if both fail** |
| **Bing Grounding returns sparse / low-quality results for a query** | **Retry with reformulated query; if still sparse, route to general-web search via Playwright over canonical fact-check sites** |
| Multimodal accuracy fails pilot gate | Ship text-only; skip image-bearing tweets with explanatory reply |
| Azure region outage / Foundry latency spike | Wall-clock cap per claim already enforced; failed invocations replied to with "couldn't fact-check in time" |
| Overstated effect expectations | Single reply will not approach Costello's 20% reduction (8-minute dialogue) |

## 11. Locked Decisions

| Decision | Locked value |
|---|---|
| Number of claims per reply | Headline only (one `is_central` claim per tweet) |
| Satire boundary | Targets claim and source, not person; no profanity, slurs, demographic or appearance-based attacks |
| Primary outcome population | Recruited participant only in v1 |
| Backend pipeline | Custom agent loop; no Loki bake-off |
| Multimodal scope | Tiers 1–3 ship; Tier 4 NEI; pilot-gated fallback to text-only |
| Source-reliability mechanism | Four-step lookup (IFCN → Wikipedia RSP → MBFC → model prior + meta-search) |
| Verdict rule threshold | ≥ 2 sources at `fact-checker` or `reputable-news` tier on central claim |
| Stage 4.5 architecture | One reasoning call with internal three-lens cascade |
| Reasoner / VLM | Claude Sonnet via Azure AI Foundry |
| Web search | Bing Grounding (Azure AI Foundry) |
| Reverse image search | Home-built. Track A description-grounded (ships v1). Track B vector index (Day-3 stretch). |
| Headless fetch layer | Playwright + headless Chromium |
| Storage | Azure Blob (frozen objects, immutable) + Azure Cosmos DB (outcomes, index) |
| Compute | Azure Container Apps |
| Secrets | Azure Key Vault |
| Monitoring | Azure Monitor + Application Insights |
| Veracity score | None. Verdict label only. |

---

## Appendix A: Worked example — Rosa Camfield

**Claim (target tweet):** "Photo shows a 101-year-old woman who has given birth to her 17th child." Attached image: an elderly woman holding a newborn.

### Stage 1.5 Tier 3 Track A (description-grounded):
- Claude VLM description: "An elderly woman with white hair and glasses, seated indoors, cradling a very small newborn baby wrapped in a light-colored blanket. Domestic interior setting."
- Extracted entities: "elderly woman", "newborn baby", "white hair", "glasses", "indoor setting", "light blanket".
- Bing Grounding query: `"101 year old woman" "newborn" baby photograph fact-check site:snopes.com OR site:thequint.com OR site:africacheck.org OR site:factcheck.org`
- Returns: Snopes, The Quint, Africa Check, Vishvas News articles, all describing this exact image.

### Stage 4.5 emits (excerpt):

```json
{
  "consolidated_findings": {
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

**Satirical:** "Ten seconds with reverse image search: she's a great-grandmother, not a mother. The 'mother of 17 at 101' story came from a satire site that says it makes things up. Snopes and The Quint covered this years ago."

All three reference the same facts, cite the same sources in the same order, and state the same counter-fact. The satirical rendering mocks the claim and the satirical source, not the target user. The invariance contract holds because the renderer only saw `presentation_payload` and `tone_neutral_justification`.