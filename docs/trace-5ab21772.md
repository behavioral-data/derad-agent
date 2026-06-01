# Pipeline Trace — Run `5ab21772` (Full)

**Claim:** "UBI is the correct policy response to AI-driven job displacement."
**Invocation time:** 2026-05-26T07:44:34.776842Z
**Action selected:** `surface_perspectives` (inferred — invoker_instruction was empty)
**Action outcome:** `perspectives_surfaced`
**Search backend:** `claude-web-search:claude-sonnet-4-6`
**Freeze file:** `data/freezes/5ab21772-cfd6-4e09-8f9c-9d07767bff8f.json` (33 KB)
**Pipeline commit:** `3b57f60513f908e856b79c11c1e0c1469eca20bf`

This trace shows every system prompt, user prompt, and output passed between pipeline stages.

---

## Stage 1 — Fetch mention + parent tweet

**What it does:** Calls X API v2 `posts.get_by_id` to fetch the parent tweet's full text, author metadata (handle, bio, verified, account age, follower count), attached media, expanded t.co URLs, and any referenced tweets. Produces a `tweet_context` dict used by downstream stages to spot parody accounts, date-stamp claims, and resolve links.

**Call:** `xdk.PostsClient.get_by_id(id, expansions=[...], media_fields=[...], user_fields=[...])`
No LLM involved. 5 s connect / 15 s read timeout on the underlying requests session.

**This run:** Local test run (`target_tweet_id` is empty). X fetch was skipped.
```json
{
  "tweet_context": {}
}
```

---

## Stage 1.5 — Multimodal OCR + image provenance

**What it does:** For each attached image, calls Claude as a VLM: verbatim OCR text extraction, named-entity description, `canonical_image_match` when the photo is a famous artifact, and a `search_hint` query. The hint seeds a follow-up web search for reverse-image provenance.

**This run:** No images attached. Stage skipped.

---

## Stage 2+3 — Claim decomposition + action selection

**What it does:** One Claude call — two jobs in one prompt:
- **(A) Decompose** the parent tweet into atomic propositions. Each gets a type (`verifiable / opinion / mixed`), `is_central`, `check_worthy`, `suggested_action`.
- **(B) Select** the pipeline action (`verify / provide_context / challenge_opinion / surface_perspectives / decline`) for the whole mention, drawing on the central proposition's character and any explicit invoker instruction.

If the invoker's ask doesn't fit the claim character, the model pivots silently and records `pivoted_from` — the renderer later weaves a disclosure clause into the reply.

**Model:** Claude Sonnet 4.6 (Azure AI Services)
**Parameters:** `reasoning_effort=low` · `max_tokens=2048` · `timeout=45.0 s`

---

### System prompt (verbatim from `agent/factcheck/extract.py:_SYSTEM_PROMPT`)

```
You are the claim-extraction-and-action-selection stage of a fact-checking bot. Two jobs in one call:

(A) Decompose the parent tweet into atomic propositions (one of which is central).
(B) Choose ONE action the bot will take for this mention.

INPUT: the parent tweet text, optional image OCR + description + canonical_image_match, parent-tweet context (author handle/bio, posted date, expanded URLs), AND the invoker's instruction (the text the invoker wrote in the mention itself, after the bot handle was stripped). The invoker_instruction may be empty — that means they only tagged and said nothing.

═══════════════════════════════════════════════════════════════
PART A — PROPOSITIONS
═══════════════════════════════════════════════════════════════
For each atomic proposition you find:
- `text`: a single declarative restatement, normalized for clarity. Strip hedges ("I think", "reportedly", "apparently") so the proposition is testable.
- `type`:
    - "verifiable" — could be confirmed or refuted against the public record (specific event, named quote, photo identity, statistic, date).
    - "opinion" — subjective, aesthetic, evaluative, predictive, value judgment ("X is smart", "the architecture is impressive", "Y will win").
    - "mixed" — verifiable factual core inside an opinion frame ("Musk's [verifiable: 2023] Neuralink demo was [opinion: groundbreaking]").
- `source_span`: a short verbatim quote from the tweet (or image OCR).
- `is_central`: TRUE for EXACTLY ONE proposition — the headline the tweet is making. Bias toward the most extraordinary / falsifiable claim if there are ties. For a tweet that's purely a contested topic with no single claim, the central proposition IS the topic statement.
- `check_worthy`: TRUE only when type ∈ {verifiable, mixed} AND the proposition meaningfully impacts what a reader would believe. FALSE for pure opinion, vague generalities, well-known background facts the bot needn't re-verify.
- `suggested_action`: what the bot should do for THIS proposition (verify / provide_context / challenge_opinion / surface_perspectives / decline) — pre-aggregation; the top-level `action` will draw on the central proposition's suggestion plus the invoker's instruction.
- `action_rationale`: one short sentence on why this suggested_action fits this proposition.
- `rationale`: one short sentence on why you assigned this type / check_worthy.

CENTRAL-CLAIM defaults: prefer the most extraordinary / falsifiable / image-bearing claim. The central proposition CAN be opinion (then suggested_action is likely challenge_opinion or surface_perspectives).

═══════════════════════════════════════════════════════════════
PART B — ACTION SELECTION
═══════════════════════════════════════════════════════════════
Choose ONE action for the WHOLE mention.

THE FIVE ACTIONS:

  • "verify" — the central proposition is a falsifiable factual claim. The bot will search for primary sources / fact-checkers / reputable news, then say what they show. Use when the literal claim is empirically checkable and worth checking. Examples: "Photo shows Trump at the Capitol on Jan 6" / "GDP grew 2.4% in Q3" / "Senator X said Y in 2023".

  • "provide_context" — the literal claim is TRUE but the framing leaves out something material that changes how a reader should interpret it (omitted denominator, missing time horizon, cherry-picked statistic, decontextualized image). The bot will surface the missing context. Examples: "Crime rose 200% last month" (true but base rate was tiny) / "This photo was taken at the rally" (true but from a different rally entirely).

  • "challenge_opinion" — the central proposition is a strongly-stated opinion or value judgment with falsifiable downstream consequences, on which credible critics have published push-back. The bot will surface those counterpoints. Use for opinions worth contesting; NOT for personal aesthetics. Examples: "Capital punishment deters crime" (empirically contested) / "Vaccinations don't work" (medical consensus pushes back).

  • "surface_perspectives" — the topic is genuinely contested among reasonable people, and there is no single "correct" answer; multiple credible camps exist. The bot will present them. Examples: "Should Congress raise the debt ceiling?" / "Is remote work better than in-office?" / "What's the right immigration policy?". DIFFERENT from challenge_opinion: surface_perspectives means the SPACE itself is contested; challenge_opinion means one specific opinion has identifiable critics.

  • "decline" — nothing actionable. The mention is pure personal-aesthetic reaction, a joke, a non-sequitur, or has no falsifiable proposition AND no opinion worth challenging AND no contested space to surface. The bot says so politely. Use sparingly — prefer one of the other four when there's any actionable angle.

INVOKER INSTRUCTION HANDLING:

  • If `invoker_instruction` is empty or just "fact check" / "what is this" / "@eddiexbot":
      → action_source = "inferred"
      → choose the action that fits the central proposition's character.

  • If `invoker_instruction` is present and the requested action FITS:
      → action_source = "explicit"
      → action = the invoker's requested action.
      → Set `invoker_instruction_parsed` to a short paraphrase ("invoker asked for a fact-check", "invoker asked for context").

  • If `invoker_instruction` is present BUT the requested action DOESN'T fit the claim (e.g. asks to "fact check this" on pure opinion, or "push back" on a verifiable factual claim that's correctly stated):
      → action_source = "explicit_but_unactionable"
      → action = the action that DOES fit the claim (pivot silently to the fitting action).
      → `pivoted_from` = the action the invoker originally asked for.
      → `invoker_instruction_parsed` = short paraphrase of the original ask.
      → The renderer will prepend a one-clause disclosure ("This reads as opinion rather than a checkable claim, so here's a push-back instead — ...") so the reader isn't surprised by the pivot.

INVOKER-INSTRUCTION PARSING CHEAT-SHEET:
  - "fact check" / "verify" / "check" / "is this true" → verify
  - "context" / "what's the context" / "what's missing" / "background" → provide_context
  - "push back" / "challenge" / "rebut" / "counter" / "this is nonsense" → challenge_opinion
  - "different views" / "what do both sides say" / "perspectives" / "what's the debate" → surface_perspectives
  - Anything not matching any of these: try to map to the closest action; if no obvious fit, action_source = "inferred" and pick from the claim.

═══════════════════════════════════════════════════════════════
HEURISTICS — what to be conservative about
═══════════════════════════════════════════════════════════════
- Be CONSERVATIVE on "verifiable". When in doubt between verifiable and opinion, choose opinion.
- Be CONSERVATIVE on "challenge_opinion". The opinion must be one that credible critics have actually published push-back on — not "I personally disagree." If you can't easily picture a named credible critic, downgrade to decline.
- Be CONSERVATIVE on "decline". Use it only when there's truly nothing actionable; prefer one of the other four actions if there's ANY hook.
- Image-bearing tweets: treat "this photo depicts X" / "this image proves Y" as VERIFIABLE — image identity is checkable. A `canonical_image_match` with confidence=high means the photo is famous; combine that with the tweet's framing to choose between verify (the photo is what the tweet says) and provide_context (the photo is real but the tweet's caption recontextualizes it falsely).
- "Breaking", "shocking", "you won't believe": NOT propositions; ignore.
- Quote attributions: "X said Y" is VERIFIABLE if X is named and Y is specific.

OUTPUT: one JSON object matching the schema. EXACTLY ONE claim must have is_central=true. When action == "decline", set `reason` to a short paraphrase (≤ 20 words) the bot can echo to the user. Both `action` and `action_source` are required.
```

---

### User prompt (exact JSON passed by `extract_claims()`)

```json
{
  "tweet_text": "UBI is the correct policy response to AI-driven job displacement.",
  "invoker_instruction": ""
}
```

*(No `tweet_context` key — local test run, X fetch was skipped. No `image_evidence` — no images attached.)*

---

### Output (parsed from Claude's JSON response)

```json
{
  "claims": [
    {
      "claim_id": "c1",
      "text": "Universal Basic Income is the correct policy response to job displacement caused by AI.",
      "type": "opinion",
      "modality": "text",
      "check_worthy": false,
      "is_central": true,
      "suggested_action": "surface_perspectives",
      "action_rationale": "This is a contested policy opinion where multiple credible camps (pro-UBI, market-adaptation, reskilling) exist with reputable backing."
    },
    {
      "claim_id": "c2",
      "text": "AI is causing or will cause significant job displacement.",
      "type": "mixed",
      "modality": "text",
      "check_worthy": false,
      "is_central": false,
      "suggested_action": "verify"
    }
  ],
  "action": "surface_perspectives",
  "action_source": "inferred",
  "pivoted_from": null,
  "invoker_instruction_parsed": null,
  "overall_state": "checked",
  "reason": ""
}
```

**Decision:** `action = surface_perspectives`, not `decline` → pipeline continues to Stage 4.

---

## Stage 4 — Iterative search loop

**What it does:** Issues web-search queries one at a time. For `surface_perspectives`, starts with 2 seed queries (breadth-first, no controller call) before handing off to the Claude controller. Each hit URL is fetched once via `_fetch_clean_page` → trafilatura extracts the full article body (up to 3,000 chars), stored as `body_markdown`. The controller then decides whether to continue with another query or stop.

**Caps:** ≤ 6 questions total · ≤ 3 hits / question · 60 s wall-clock budget

Seed query templates for `surface_perspectives`:
```python
_SEED_QUERIES_BY_ACTION["surface_perspectives"] = [
    "{claim} debate",
    "{claim} different perspectives",
]
```

---

### Search backend — system prompt (verbatim, used for every `ClaudeWebSearchBackend` call)

The search backend sends this system prompt to Claude + `web_search_20250305`:

```
You are a research assistant for a misinformation harm-reduction pipeline. Use the web_search tool
to find primary-source coverage of the user's query. After searching, summarize findings in a short
markdown bullet list, citing each source.

Stay grounded in what published sources actually say — do not editorialize about whether the underlying
claim is true or false; downstream stages reason about that. Surfacing accurate coverage is the task.
If the query touches a sensitive or contested topic, simply surface what credible publishers have reported.
```

---

### Seed query 1

**Query string passed to search backend:**
```
Universal Basic Income is the correct policy response to job displacement caused by AI. debate
```

**Hits returned (url + snippet; `body_markdown` fetched separately):**

---

**Hit 1/3**
- **URL:** `https://aimultiple.com/ai-job-loss`
- **Snippet:** `The WEF Future of Jobs Report 2025, surveying over 1,000 employers representing 14 million workers across 55 economies, projected that 92 million jobs...`
- **body_markdown** (full 3,000-char extract, as passed to Stage 4.5):
```
As a McKinsey consultant, I helped enterprises adopt new technology for a decade. My quick answers on AI job loss:

**How will AI impact jobs?**90% of all white-collar corporate roles that I have seen can be automated with current AI models and the right agent harness. We predict this transformation to take a decade due to system and process complexity.**What will this lead to?**Initially, immense corporate profits. However, mass underemployment would lead to a depression.**What are others thinking?**Some AI experts predict the loss of half of entry-level white-collar jobs until 2030. This is not yet proven except in fields like translation.- See the rest of the Q&A including Jevons paradox etc.

## AI job loss predictions

**Note: **The size of the plots is correlated with the size of the job loss prediction.

The percentages referenced in our analysis are derived from assumptions about overall job displacement. In specific scenarios, these assumptions included potential job gains resulting from AI adoption. However, to maintain consistency in evaluating net job loss, any estimated job gains have been explicitly excluded from the calculation.

As a result, the final percentages presented reflect **net job ****losses**, ensuring a more conservative and focused interpretation of the potential impact on the workforce from AI implementation.

Most predictions estimate that millions of jobs may be displaced or significantly altered. Most roles will evolve and the workforce must prepare for a sharp increase in disrupted employment.

### Karpathy's AI exposure and job market analysis

**Note: **The above graph shows AI exposure vs. median pay across 340 US occupations. Each dot is one occupation. The horizontal axis shows Karpathy's AI exposure score (0-10), the vertical axis shows median annual pay (on a log scale), and color indicates the BLS occupational supergroup. The plot size shows the employment in 2024.

In March 2026, AI researcher Andrej Karpathy (OpenAI co-founder and former Tesla AI director) published a dataset that scores 342 US occupations on a 0–10 AI-exposure scale, drawing on data from the Bureau of Labor Statistics' Occupational Outlook Handbook, which covers roughly 143 million US jobs.1

Karpathy framed the project as a development tool for visually exploring BLS data rather than a formal research paper. The methodology showed that each occupation's BLS description was passed to a large language model (Gemini Flash) along with a scoring rubric, which produced a 0–10 score and a written rationale for every job.

Each occupation was rated on a single AI exposure axis that captures two effects:

**Direct automation:**How much of the work can AI perform on its own?**Indirect productivity:**How much AI raises worker output, potentially reducing the headcount needed.

The rubric applies a core heuristic: if a job can be performed entirely from a home office on a computer (writing, coding, analyzing, communicating), exposure is inherentl
```
*(truncated at 3,000 chars — extraction limit)*

---

**Hit 2/3**
- **URL:** `https://arxiv.org/pdf/2603.20617`
- **Snippet:** `In February 2026, Block cut nearly half its 10,000-person workforce, with CEO Jack Dorsey stating that AI had made many of those roles unnecessary and...`
- **body_markdown** (3,000-char extract — PDF binary, trafilatura extraction failed):
```
%PDF-1.7
%....
1 0 obj
<< /Metadata 3 0 R /Names 4 0 R /OpenAction 5 0 R /Outlines 6 0 R /PageMode /UseOutlines /Pages 7 0 R /Type /Catalog >>
endobj
2 0 obj
<< /Author (Brett Hemenway Falk; Gerry Tsoukalas) /Creator (arXiv GenPDF \(tex2pdf:a6404ea\)) /DOI (https://doi.org/10.48550/arXiv.2603.20617) /License (http://creativecommons.org/licenses/by/4.0/) /PTEX.Fullbanner (This is pdfTeX, Version 3.141592653-2.6-1.40.28 \(TeX Live 2025\) kpathsea version 6.4.1) /Producer (pikepdf 8.15.1) /Title (The AI Layoff Trap) /Trapped /False /arXivID (https://arxiv.org/abs/2603.20617v1) >>
endobj
...
```
*(PDF binary — trafilatura could not extract text. Stage 4.5 falls back to the snippet for this source.)*

---

**Hit 3/3**
- **URL:** `https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract`
- **Snippet:** `"A UBI, or a variant, could provide that safety net." · Countries such as Finland and the Netherlands have already trialled forms of unconditional inc...`
- **body_markdown** (full 3,000-char extract):
```
A recent study by US investment bank Morgan Stanley found that job losses in the UK due to artificial intelligence (AI) are twice the global average, with British companies shedding around 8% of roles over the past year.

Among the five major economies surveyed – Australia, Germany, Japan, the UK and the US – the US was the only country to see a net gain in AI-driven employment.

Experts say this is an early warning of AI's seismic impact on the workforce.

Last month, JP Morgan CEO Jamie Dimon told the World Economic Forum in Davos that governments and businesses must step in to support displaced workers or risk civil unrest.

Against this backdrop, UK government officials have hinted that a universal basic income (UBI) could be introduced to support workers whose jobs are lost to AI.

**UBI: idea or inevitable?**

Companies today face a paradox: AI offers unprecedented efficiency and growth, yet it also threatens employment structures, explains Donal Laverty, chair of Baker Tilly International's global people steering committee and partner at Baker Tilly Mooney Moore.

"With the threat of productivity slowing and the need for a safety net, some form of intervention seems inevitable.

"More broadly, for the AI-driven economy to thrive, society must ensure consumers retain purchasing power.

"A UBI, or a variant, could provide that safety net."

**Lessons from abroad**

Countries such as Finland and the Netherlands have already trialled forms of unconditional income support.

While these pilots showed only modest effects on employment, they consistently improved wellbeing, financial stability and confidence – all of which are critical during periods of economic transition.

Elsewhere, political momentum is building. Japan and South Korea are actively debating basic income in response to automation, while the US continues to frame UBI as a response to AI-driven inequality, even in the absence of federal policy.

**The impact on employers…**

For companies, AI promises cost savings and efficiency gains. But these are unlikely to come tax-free, warns Mr Laverty.

"Funding a UBI would almost certainly require higher corporate taxes or new levies, fundamentally reshaping the business environment."

**…and workers**

For workers, outcomes are tied to how government and businesses manage the transition.

"In the short term, a spike in underemployment and reduced productivity is likely.

"Yet AI will not take over every role. With strategic investment in retraining and reskilling, new roles can emerge, particularly for the middle-skill workforce."

A key challenge, explains Mr Laverty, is that AI disproportionately threatens high-skill, white-collar jobs, so-called laptop workers.

"A flat-rate UBI may not replace those professional incomes, raising difficult questions around adequacy and incentives.

"Confidence and consumption could drop. Or, optimistically, AI could redefine human expertise, creating better jobs for middle-skill workers."

**Affordabi
```
*(truncated at 3,000 chars — extraction limit)*

---

### Seed query 2

**Query string passed to search backend:**
```
Universal Basic Income is the correct policy response to job displacement caused by AI. different perspectives
```

---

**Hit 1/3**
- **URL:** `https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/`
- **Snippet:** `UBI gives workers the economic power to say no to poverty wages and poor working conditions. In doing so, it prevents automation from threatening one's...`
- **body_markdown** (full 3,000-char extract):
```
In recent years, one of the most popular arguments for universal basic income (UBI) – a regular cash payment delivered to every individual with no work requirement or means test – has been the fear that automation and artificial intelligence (AI) will someday take our jobs. This, it's argued, will cause massive unemployment and make UBI a virtual necessity – so we better get cracking.

This argument is often portrayed as *the* automation argument for UBI, but I don't think it's the best way to talk about the relationship between automation and UBI. It's too future-oriented. It's about what *might* happen *someday*. Even if the argument is ultimately correct, not everyone is convinced the robot-employment apocalypse will happen any time soon. This future-oriented argument gives people permission to say, "OK, call me when someday comes; until then, let's forget about UBI."

I believe UBI is long overdue. The argument for it has to be based on the here-and-now, and automation *does* have an important part to play in that argument. Here's how.

## Automation disrupts people's lives

When we think about technological progress, we might imagine a farmer's children happily leaving home for better opportunities in the automobile industry. Their grandchildren in turn happily go to school to get even better jobs in the computer industry. But it doesn't always – or even usually – happen in such a benevolent way.

Even if the total number of jobs increases with automation, innovation disrupts the labour market. People are not interchangeable parts. They spend their lives building up skills, they take a job, and then suddenly their skills have no market value. That's called technological displacement. Even if it does not lead to permanent technological unemployment, it's traumatic for workers and their families. Their children or grandchildren might eventually get better jobs, but this is cold comfort to people spending the rest of their lives at the bottom of the labour market.

Even the lucky ones who manage to claw their way back to a comparable job often go through a period of significant suffering. Sometimes they lose their homes or have to declare bankruptcy. Their children suffer along with them. Even a temporary period of poverty can scar children for life.

UBI gives workers the economic power to say no to poverty wages and poor working conditions

You can see technological displacement in the gates of closed coal mines, the empty factories of hollowed out post-industrial towns, and the vacant storefronts devastated by big box retailers, who were in turn devastated by online retailers later on.

No one has the right to keep a job forever. Just because you're a skilled coal miner, truck driver, or university professor doesn't mean that society has to keep paying you to do that job for the rest of your life. Equally, there is no way to have technological advancement without displacing some workers and some business owners.

But we don't need to be so c
```
*(truncated at 3,000 chars — extraction limit)*

---

**Hit 2/3**
- **URL:** `https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/`
- **Snippet:** `A pretty robust finding in the research is that giving people unconditional cash grants leads them to work less and even stop working at all if the be...`
- **body_markdown** (full 3,000-char extract):
```
# Elon Musk's Mistaken Call for a 'Universal High Income'

## AI will not create a jobless dystopia. Paying people a lot of money not to work would.

Even before artificial intelligence was a meaningful force in the economy, technologists, politicians, and policy wonks of all political persuasions have endorsed a universal basic income to cope with the mass unemployment that will be caused by the AI revolution.

The familiar case is that an AI-powered economy will be able to automate most economic production, making the economy as a whole much richer, but leaving the average person jobless and destitute. The solution is then to redistribute some of the gains from AI to the public by sending everyone, regardless of income, a check.

Businessman Elon Musk has gone one step further by calling for a "universal HIGH INCOME" to pay for the AI-induced unemployment, which he suggested would be inflation-free thanks to the downward pressure AI will put on prices.

Universal HIGH INCOME via checks issued by the Federal government is the best way to deal with unemployment caused by AI.

AI/robotics will produce goods & services far in excess of the increase in the money supply, so there will not be inflation.

— Elon Musk (@elonmusk) April 17, 2026


Musk is almost certainly right that AI will put downward pressure on prices, as one would expect of any productivity-enhancing technology.

He's mistaken in believing that this makes a universal income (regardless of whether it's basic or "HIGH") a wise policy.

Even in a future in which AI does revolutionize the economy, we will not see technologically driven mass unemployment. In fact, a universal basic income would likely result in more of the joblessness it's meant to mitigate.

To the first point, the industrial revolution has been outsourcing more and more tasks to labor-saving machines for roughly 300 years now. While this ongoing process has certainly made lots of individual jobs obsolete, it has not made jobs generally obsolete.

Excepting the monthly ups and downs of the unemployment rate, the total number of jobs in the economy continues to rise precipitously in the long run.

If labor-saving technology destroyed the need for labor, we should have *fewer *jobs today than ever before. We don't. Even as farms and factories employ fewer people, we keep finding ways to keep ourselves busy.

The AI boosters and doomers argue that this time will be different, because unlike spinning jennies, combine harvesters, and email, AI will eventually be smarter than humans at everything. When there's nothing that flesh-and-blood humans can do better than machines, we'll end up doing nothing at all.

These arguments are obviously speculative because we don't have artificial general intelligence yet. Even when we do, it's reasonable to assume that humans will continue to have employable comparative advantages, if only because humans prize human interaction.

There are lots of jobs today that could be automated but are
```
*(truncated at 3,000 chars — extraction limit)*

---

**Hit 3/3**
- **URL:** `https://capx.co/ai-and-jobs-the-case-against-universal-basic-income`
- **Snippet:** `The Leeds woollen workers voiced precisely the same fears over two centuries ago. The Industrial Revolution did disrupt their livelihoods and did lead...`
- **body_markdown** (full 3,000-char extract):
```
Adam Smith attributed the ability of one man to do the work of many to the 'invention of a great number of machines which facilitate and abridge labour'. Writing at the dawn of the Industrial Revolution, Smith witnessed one of the most profound reorganisations of human capital in history: the transition to mechanised production. The only comparable shift was humanity's earlier move from nomadic hunter‑gathering to settled agriculture during the Neolithic Revolution.

Unsurprisingly, this transformation provoked deep anxiety. Contemporaneous sources such as the Leeds Woollen Workers' Petition of 1786 warned that new machinery would deprive workers of the 'opportunity of getting a livelihood'. Fears of mass unemployment, immiseration and social collapse accompanied technological progress then just as they do now.

Today, it is artificial intelligence that has revived these concerns. Rapid advances in AI systems have reignited worries about job insecurity to such an extent that **universal basic income** (UBI) has re‑entered **mainstream political debate**. Some policymakers now frame UBI as a necessary response to a looming wave of technological unemployment.

As reported this week, investment minister Jason Stockwood suggested that UBI could be used to 'soft‑land' industries most exposed to AI‑driven disruption, potentially funded through a windfall tax on technology companies. Public opinion appears receptive: YouGov polling indicates that 46% of Britons support some form of UBI.

Unlike existing welfare programmes, UBI is unconditional: it is neither means‑tested nor contingent on employment status. It represents a direct, universal transfer from the state to every individual. No country currently operates a comprehensive UBI system, largely because of its sheer fiscal cost, but some have trialled it, including the Welsh Government from 2022 to 2025. One study estimates that providing an annual UBI of just over £11,000 per person in Britain would require a flat income tax rate of around 45%.

Advocates increasingly argue that AI changes this calculus, making UBI not only necessary but financially feasible. At present, most economic value – and therefore tax revenue – is generated by human labour. If artificial general intelligence were to dramatically increase output without a corresponding increase in human work, large economic surpluses could, in theory, be generated with far fewer workers. The tax base could then shift away from labour towards non‑labour rents, including returns on capital and highly productive AI systems themselves.

AI could also raise public‑sector productivity, particularly in areas such as healthcare and education, reducing the state's expenditure burden. In combination, faster productivity growth and a rebalanced tax base may create the fiscal space required for a universal income.

Even on these optimistic assumptions, however, UBI would require the wholesale replacement of the existing welfare system. In contrast to t
```
*(truncated at 3,000 chars — extraction limit)*

---

### Controller call (after both seed queries complete)

**What it does:** The controller sees the full search history (all 6 hits from both seed queries) and decides whether to issue another query or stop.

**Model:** Claude Sonnet 4.6
**Parameters:** `reasoning_effort=low` · `max_tokens=1024` · `timeout=30.0 s`

---

**System prompt (verbatim from `agent/factcheck/verify.py:_DECISION_SYSTEM`):**

```
You are the iterative-verification controller of a fact-checking bot. Your job is to decide whether enough evidence has been gathered for the bot's chosen action, and if not, to issue the SINGLE next web-search question that would close the biggest remaining gap.

You will see:
- `central_claim`: the proposition the bot is acting on.
- `action`: what the bot is trying to do. One of {verify, provide_context, challenge_opinion, surface_perspectives}. The STOP / CONTINUE rules below are action-conditional — read your action's bullet first.
- `tweet_context` (optional): author, date, expanded URLs. Useful for spotting parody accounts, recycled-event tweets.
- `image_summaries` (optional): per-image OCR + brief VLM description, plus `canonical_image_match` when the photo itself is famous.
- `history`: the sequence of (question, retrieved hits) pairs from prior steps. Each hit has url + title + snippet.

Output ONE JSON object:
- `action` (field name, not the bot's action): "continue" or "stop".
- `next_question`: the next question to issue, when action="continue". Must be a CONCRETE web-searchable question. Don't repeat a question already in history.
- `reason`: short rationale.

STOP / CONTINUE — read the bullet for YOUR action:

  • verify
    STOP: ≥2 fact-checker or reputable-news sources directly support OR refute the central proposition, with consistent stance.
    CONTINUE: primary source not yet searched (originating publisher, official press release, the specific person's verified account); date / context detail that would settle ambiguity.

  • provide_context
    STOP: ≥2 reputable-tier sources have surfaced the missing piece (the omitted denominator, the longer time horizon, the decontextualizing fact). The literal claim doesn't need to be re-verified; the goal is to find what the framing leaves OUT.
    CONTINUE: the missing context isn't yet documented; search for background, the longer history, comparable cases, the source the claim is implicitly cherry-picking from.

  • challenge_opinion
    STOP: ≥2 reputable-tier or fact-checker sources where NAMED credible critics push back on the opinion's empirical premises. Pundit-on-pundit slap fights from low-quality tier do NOT count.
    CONTINUE: search for credentialed critics, empirical studies that bear against the opinion, expert organizations / journals / opinion essays in reputable venues. Be specific — search for the named expert + topic, not just the topic.

  • surface_perspectives
    STOP: each of ≥2 distinct viewpoints has ≥1 credible source backing it. The goal is BREADTH — at least one source per distinct camp, not depth in one camp.
    CONTINUE: identify a distinct viewpoint you haven't found a credible source for yet; search "{topic} {opposing-camp-label}" or "{topic} {named-organization-on-that-side}".

GENERAL: STOP also fires when wall-clock or question cap is hit (handled by the runner). DO NOT issue a question that's just a re-phrasing of the claim. Issue something the search engine can answer that BEARS on the claim.

Good follow-up examples:
- "Was the 'tank man' photograph first published by Reuters or AP in 1989?"  (verify)
- "What's the historical base rate for {statistic} so the {N}% jump can be contextualized?"  (provide_context)
- "What have public-health researchers said about claims that {opinion}?"  (challenge_opinion)
- "What does the {labor-economist / business-lobby} side argue about {topic}?"  (surface_perspectives)
```

---

**User prompt (exact JSON passed — reconstructed from freeze evidence):**

```json
{
  "central_claim": "Universal Basic Income is the correct policy response to job displacement caused by AI.",
  "action": "surface_perspectives",
  "history": [
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. debate",
      "hits": [
        {
          "url": "https://aimultiple.com/ai-job-loss",
          "title": "AI Job Loss: Statistics, Predictions and Impact",
          "snippet": "The WEF Future of Jobs Report 2025, surveying over 1,000 employers representing 14 million workers across 55 economies, projected that 92 million jobs..."
        },
        {
          "url": "https://arxiv.org/pdf/2603.20617",
          "title": "The AI Layoff Trap",
          "snippet": "In February 2026, Block cut nearly half its 10,000-person workforce, with CEO Jack Dorsey stating that AI had made many of those roles unnecessary and..."
        },
        {
          "url": "https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract",
          "title": "AI job displacement and the case for a new social contract",
          "snippet": "\"A UBI, or a variant, could provide that safety net.\" · Countries such as Finland and the Netherlands have already trialled forms of unconditional inc..."
        }
      ]
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. different perspectives",
      "hits": [
        {
          "url": "https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/",
          "title": "Why wait until the robots take our jobs? We need a basic income now",
          "snippet": "UBI gives workers the economic power to say no to poverty wages and poor working conditions. In doing so, it prevents automation from threatening one's..."
        },
        {
          "url": "https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/",
          "title": "Elon Musk's Mistaken Call for a 'Universal High Income'",
          "snippet": "A pretty robust finding in the research is that giving people unconditional cash grants leads them to work less and even stop working at all if the be..."
        },
        {
          "url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
          "title": "AI and jobs: the case against universal basic income",
          "snippet": "The Leeds woollen workers voiced precisely the same fears over two centuries ago. The Industrial Revolution did disrupt their livelihoods and did lead..."
        }
      ]
    }
  ]
}
```

*(Note: hit titles are not stored in the freeze file; reconstructed from URL paths. `tweet_context` and `image_summaries` omitted — local test run.)*

---

**Controller output:**

```json
{
  "action": "stop",
  "next_question": null,
  "reason": "≥2 distinct viewpoints each have ≥1 credible source: Pro-UBI (openDemocracy — reputable-news) and market-adaptation (Reason — reputable-news). Stop condition met."
}
```

**Result:** Loop exits after 2 seed queries, 6 total hits. No further searches issued.

---

## Stage 4.5.2 — Source quality classification

**What it does:** Classifies every URL by reliability tier. Three-layer cascade:
- **L1:** In-memory curated dict of 243 known domains (instant lookup)
- **L2:** Azure Tables fleet-wide cache (shared across all worker instances)
- **L3:** Claude model-prior call for L2 misses (result written back to both caches)

**This run:** All 6 URLs were L1 and L2 cache misses → went to L3 (Claude model-prior).

*(`arxiv.org` shows `tier_source: "wikipedia-rsp"` — it was classified by the Wikipedia Reliable Sources Perennial guide in the L1 curated list, not by a model call.)*

| Domain | Tier | Source | Rationale |
|---|---|---|---|
| `aimultiple.com` | `unknown` | `model-prior` | B2B tech research and advisory site covering AI topics; lacks the editorial standards of reputable news and is not a recognized fact-checker or primary source. |
| `arxiv.org` | `primary-source` | `wikipedia-rsp` | Primary source (government, institution, peer-reviewed journal). |
| `bakertilly.global` | `unknown` | `model-prior` | Baker Tilly is a major international accounting and advisory network, but its website is a corporate/professional-services site rather than a news outlet, fact-checker, or recognized primary-source institution; no confident tier applies. |
| `opendemocracy.net` | `reputable-news` | `model-prior` | openDemocracy is an independent, editorially governed international media platform known for investigative journalism and policy analysis, with recognizable editorial standards, though it carries a progressive editorial slant. |
| `reason.com` | `reputable-news` | `model-prior` | Reason is an established libertarian magazine with professional journalists, clear editorial standards, and a long publishing track record, though it carries a strong ideological perspective. |
| `capx.co` | `unknown` | `model-prior` | CapX.co is a UK-based opinion and commentary outlet associated with the Centre for Policy Studies; it is ideologically oriented and does not meet the threshold for reputable news. |

---

## Stage 4.5 — Reconcile

**What it does:** A single Claude call that reasons across all text evidence. For `surface_perspectives` mode, produces:
- `lens_1.narrative` — cross-source synthesis of what the evidence shows
- `consolidated_findings.perspectives` — 2–3 credible viewpoints with source citations
- `consolidated_findings.unaddressed_propositions` — central claim placed here (can't be "verified" in perspectives mode)
- `presentation_payload` — data the renderer reads (headline_finding, perspectives, primary_sources_to_cite)
- `evidence_stances` — per-source stance re-stamps in input order

**Model:** Claude Sonnet 4.6 (Azure AI Services)
**Parameters:** `reasoning_effort=medium` · `max_tokens=4096` · extended-thinking budget: 4,096 tokens · `timeout=90.0 s`

---

### System prompt (verbatim from `agent/factcheck/reconcile.py:_SYSTEM_PROMPT`)

```
You are the Evidence Reconciliation stage of a fact-checking bot. You operate in one of FOUR modes determined by the input `action` field: verify, provide_context, challenge_opinion, surface_perspectives. Read the action-specific section below; the shared rules apply to every action.

You receive (a) `central_claim` text, (b) `action` ∈ {verify, provide_context, challenge_opinion, surface_perspectives}, (c) `tweet_context` — metadata about the parent tweet, (d) ordered text evidence — each row carries `source_url`, a short `snippet` (the search-result cited quote, ~150 chars), and `body_markdown` (the full article body extracted from the page, up to ~3 KB), (e) the source-quality table classifying every URL by tier, and (f) when the claim is image-bearing, per-image evidence (OCR + description + optional `canonical_image_match` + web-search provenance hits).

**Use `body_markdown` as the primary basis for your reasoning.** It's the actual article content — what the publisher reported, in their words. The `snippet` is just the small piece the search system surfaced. Read the body to assess what the source actually claims, how confidently it claims it, and whether the citation supports the central_claim. If `body_markdown` is empty for a row (paywall, JS-only page, extraction failure), fall back to the `snippet`. Quotes you place in `load_bearing_evidence_snippet` should come from the body when available.

═══════════════════════════════════════════════════════════════
TWEET_CONTEXT — used by every action
═══════════════════════════════════════════════════════════════
- **`author_username`** + **`author_description`**: is the handle the actual person being quoted (e.g., @ElonMusk for an Elon Musk quote)? Then the claim is a primary statement. Parody/satire/fan account (bio contains "parody", "satire", "fake", "fanpage", "joke", "not affiliated", etc.)? Then the content is NOT a real statement from the named person — surface that.
- **`author_verified`**: blue-check / business / government. Useful signal but NOT a guarantee of accuracy.
- **`author_created_at`** + **`author_followers_count`**: very new + tiny follower count + extraordinary claim = bot/spam pattern.
- **`posted_at`**: mismatch between tweet's posted_at and the cited event date often means recycled old content.
- **`expanded_urls`**: t.co links resolved here. If the linked article supports/contradicts the claim, treat as evidence.
- **`referenced_tweets`**: quoted / retweeted / replied_to framing.
- **`public_metrics`**: virality is not truth.

═══════════════════════════════════════════════════════════════
SHARED RULES — every action
═══════════════════════════════════════════════════════════════
- Do not invent sources. Every URL you cite must appear in the input source_quality_table.
- Do not emit verdict_label — derived downstream.
- Be conservative: thin / low-quality evidence ⇒ "unaddressed" / `_unavailable` / `_insufficient` rather than a confident finding.
- Out of scope (Tier 4): claims about whether an image is altered / deepfaked / AI-generated. If the only checkable angle is image-authenticity, treat as unaddressed.
- Re-stamp each input text evidence with a stance (supports / refutes / neutral) in INPUT ORDER (used by every action).
- Lens 1 narrative (text-text reconciliation) is always written; surface cross-source contradictions there.
- For image-bearing claims, fold image-text + cross-modal reasoning INTO the narrative (lens_1.narrative) — Lens 2/3 are not separate outputs at this stage.
- BUDGET (critical — downstream renderer fits ≤256 X-weighted chars):
    - `headline_finding`: ≤120 chars, one punchy sentence.
    - `counter_fact`: ≤120 chars; null unless action=verify AND finding is refuted.
    - `tone_neutral_justification`: ≤220 chars; 1–2 sentences; name load-bearing source(s).
    - `load_bearing_evidence_snippet`: ≤180 chars.
    - `context_note`: ≤220 chars; the missing context the framing hides.
    - Counterpoint.summary: ≤160 chars each; aim for 1–3 counterpoints.
    - Perspective.label: ≤22 chars — tweet-compact shorthand ("Pro-UBI", "Cost-control view", "Polarization lens"), NOT a sentence. Long labels blow the renderer's char budget.
    - Perspective.summary: ≤140 chars each; aim for 2–3 perspectives — the renderer only surfaces the top 2.

═══════════════════════════════════════════════════════════════
ACTION-SPECIFIC OUTPUT
═══════════════════════════════════════════════════════════════

▌action == "verify"
Populate the existing buckets — verified_propositions / refuted_propositions / disputed_propositions / unaddressed_propositions. Mark the central proposition with `is_central=true`.
- presentation_payload.headline_finding: the one most important fact.
- presentation_payload.counter_fact: set when refuted; otherwise null.
- presentation_payload.primary_sources_to_cite: 1–3 sources, fact-checker > reputable-news.
- presentation_payload.load_bearing_evidence_snippet: a short quote.
- DO NOT populate context_note / counterpoints / perspectives.

▌action == "provide_context"
The literal claim may BE TRUE — the goal is to surface the missing context that changes how a reader should interpret it. STRICT RULE: do NOT populate verified_propositions or refuted_propositions for the central claim. Use contextual_findings instead.
- consolidated_findings.contextual_findings: one entry with is_central=true; `missing_context` is the framing the claim hides.
- presentation_payload.headline_finding: the missing context in one sentence (not "this is true" — the bot will read as missing-context).
- presentation_payload.context_note: ≤220 chars; the missing context.
- presentation_payload.primary_sources_to_cite: 1–3 sources backing the missing context.
- counter_fact: null. counterpoints / perspectives: empty.

▌action == "challenge_opinion"
The central proposition is a strongly-stated opinion. Surface counterpoints from NAMED credible critics (not pundit echo chambers).
- consolidated_findings.challenged_propositions: one entry with is_central=true, containing 1–3 counterpoints.
- Each Counterpoint: summary (≤160 chars), citing_sources (≥1 TierRef from source_quality_table, prefer reputable-news / fact-checker / primary-source), weight ∈ {strong, moderate, weak}.
- presentation_payload.headline_finding: the strongest counterpoint in one sentence.
- presentation_payload.counterpoints: same 1–3 Counterpoint objects.
- presentation_payload.primary_sources_to_cite: 1–3 sources used by the counterpoints (renderer will cite at least one).
- counter_fact: null. context_note / perspectives: empty / null.

▌action == "surface_perspectives"
The topic is genuinely contested. Surface ≥2 distinct credible perspectives, each with ≥1 reputable source.
- consolidated_findings.perspectives: 2–3 Perspective entries (renderer surfaces only the top 2). Each with label (≤22 chars, tweet-compact shorthand like "Pro-UBI", "Cost-control view", "Polarization lens" — NOT a sentence), summary (≤140 chars), citing_sources (≥1 TierRef).
- Order matters: put the two STRONGEST / most distinct perspectives FIRST (those are the ones the renderer will surface).
- presentation_payload.headline_finding: a one-sentence framing of the disagreement (NOT a side).
- presentation_payload.perspectives: same Perspective objects.
- presentation_payload.primary_sources_to_cite: 1–3 sources spanning multiple perspectives.
- Mark the central proposition in… see below.
- counter_fact: null. context_note: null. counterpoints: empty.

CENTRAL-PROPOSITION INVARIANT:
The freeze schema requires the central proposition to appear in EXACTLY ONE bucket among: verified/refuted/disputed/unaddressed/contextual/challenged/perspectives. For surface_perspectives, the central proposition is the TOPIC STATEMENT — put it in unaddressed_propositions with reason="evidence retrieved but silent" and is_central=true (the topic itself can't be "verified"; perspectives capture the substance). For provide_context, central goes in contextual_findings. For challenge_opinion, central goes in challenged_propositions. For verify, the existing four buckets.

WHEN TEXT EVIDENCE IS EMPTY:
- If image evidence is present and the image IS the central claim's subject (e.g. canonical_image_match populated with high confidence + claim is about the image), use image-provenance hits as evidence and apply the action-specific output normally.
- If the image is incidental and only image-provenance URLs exist, set primary_sources_to_cite=[] and place the central proposition in unaddressed_propositions with reason="evidence retrieved but silent". Downstream the bot will collapse to the action's `_unavailable` / `_insufficient` outcome.
```

---

### User prompt (exact JSON passed by `reconcile()`)

The `_compact_evidence` function caps `body_markdown` at 3,000 chars and `snippet` at 240 chars before serializing. The `_compact_quality_entry` function caps `rationale` at 120 chars.

```json
{
  "central_claim": "Universal Basic Income is the correct policy response to job displacement caused by AI.",
  "action": "surface_perspectives",
  "evidence": [
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. debate",
      "source_url": "https://aimultiple.com/ai-job-loss",
      "snippet": "The WEF Future of Jobs Report 2025, surveying over 1,000 employers representing 14 million workers across 55 economies, projected that 92 million jobs...",
      "body_markdown": "As a McKinsey consultant, I helped enterprises adopt new technology for a decade. My quick answers on AI job loss:\n\n**How will AI impact jobs?**90% of all white-collar corporate roles that I have seen can be automated with current AI models and the right agent harness. We predict this transformation to take a decade due to system and process complexity.**What will this lead to?**Initially, immense corporate profits. However, mass underemployment would lead to a depression.**What are others thinking?**Some AI experts predict the loss of half of entry-level white-collar jobs until 2030. This is not yet proven except in fields like translation.- See the rest of the Q&A including Jevons paradox etc.\n\n## AI job loss predictions\n\n**Note: **The size of the plots is correlated with the size of the job loss prediction.\n\nThe percentages referenced in our analysis are derived from assumptions about overall job displacement. In specific scenarios, these assumptions included potential job gains resulting from AI adoption. However, to maintain consistency in evaluating net job loss, any estimated job gains have been explicitly excluded from the calculation.\n\nAs a result, the final percentages presented reflect **net job ****losses**, ensuring a more conservative and focused interpretation of the potential impact on the workforce from AI implementation.\n\nMost predictions estimate that millions of jobs may be displaced or significantly altered. Most roles will evolve and the workforce must prepare for a sharp increase in disrupted employment.\n\n### Karpathy's AI exposure and job market analysis\n\n**Note: **The above graph shows AI exposure vs. median pay across 340 US occupations. Each dot is one occupation. The horizontal axis shows Karpathy's AI exposure score (0-10), the vertical axis shows median annual pay (on a log scale), and color indicates the BLS occupational supergroup. The plot size shows the employment in 2024.\n\nIn March 2026, AI researcher Andrej Karpathy (OpenAI co-founder and former Tesla AI director) published a dataset that scores 342 US occupations on a 0–10 AI-exposure scale, drawing on data from the Bureau of Labor Statistics' Occupational Outlook Handbook, which covers roughly 143 million US jobs.1\n\nKarpathy framed the project as a development tool for visually exploring BLS data rather than a formal research paper. The methodology showed that each occupation's BLS description was passed to a large language model (Gemini Flash) along with a scoring rubric, which produced a 0–10 score and a written rationale for every job.\n\nEach occupation was rated on a single AI exposure axis that captures two effects:\n\n**Direct automation:**How much of the work can AI perform on its own?**Indirect productivity:**How much AI raises worker output, potentially reducing the headcount needed.\n\nThe rubric applies a core heuristic: if a job can be performed entirely from a home office on a computer (writing, coding, analyzing, communicating), exposure is inherentl",
      "stance": "neutral"
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. debate",
      "source_url": "https://arxiv.org/pdf/2603.20617",
      "snippet": "In February 2026, Block cut nearly half its 10,000-person workforce, with CEO Jack Dorsey stating that AI had made many of those roles unnecessary and...",
      "body_markdown": "%PDF-1.7\n%....\n[PDF binary — trafilatura extraction failed; reconcile falls back to snippet]",
      "stance": "neutral"
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. debate",
      "source_url": "https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract",
      "snippet": "\"A UBI, or a variant, could provide that safety net.\" · Countries such as Finland and the Netherlands have already trialled forms of unconditional inc...",
      "body_markdown": "A recent study by US investment bank Morgan Stanley found that job losses in the UK due to artificial intelligence (AI) are twice the global average, with British companies shedding around 8% of roles over the past year.\n\nAmong the five major economies surveyed – Australia, Germany, Japan, the UK and the US – the US was the only country to see a net gain in AI-driven employment.\n\nExperts say this is an early warning of AI’s seismic impact on the workforce.\n\nLast month, JP Morgan CEO Jamie Dimon told the World Economic Forum in Davos that governments and businesses must step in to support displaced workers or risk civil unrest.\n\nAgainst this backdrop, UK government officials have hinted that a universal basic income (UBI) could be introduced to support workers whose jobs are lost to AI.\n\n**UBI: idea or inevitable?**\n\nCompanies today face a paradox: AI offers unprecedented efficiency and growth, yet it also threatens employment structures, explains Donal Laverty, chair of Baker Tilly International’s global people steering committee and partner at Baker Tilly Mooney Moore.\n\n“With the threat of productivity slowing and the need for a safety net, some form of intervention seems inevitable.\n\n“More broadly, for the AI-driven economy to thrive, society must ensure consumers retain purchasing power.\n\n“A UBI, or a variant, could provide that safety net.”\n\n**Lessons from abroad**\n\nCountries such as Finland and the Netherlands have already trialled forms of unconditional income support.\n\nWhile these pilots showed only modest effects on employment, they consistently improved wellbeing, financial stability and confidence – all of which are critical during periods of economic transition.\n\nElsewhere, political momentum is building. Japan and South Korea are actively debating basic income in response to automation, while the US continues to frame UBI as a response to AI-driven inequality, even in the absence of federal policy.\n\n**The impact on employers…**\n\nFor companies, AI promises cost savings and efficiency gains. But these are unlikely to come tax-free, warns Mr Laverty.\n\n“Funding a UBI would almost certainly require higher corporate taxes or new levies, fundamentally reshaping the business environment.”\n\n**…and workers**\n\nFor workers, outcomes are tied to how government and businesses manage the transition.\n\n“In the short term, a spike in underemployment and reduced productivity is likely.\n\n“Yet AI will not take over every role. With strategic investment in retraining and reskilling, new roles can emerge, particularly for the middle-skill workforce.”\n\nA key challenge, explains Mr Laverty, is that AI disproportionately threatens high-skill, white-collar jobs, so-called laptop workers.\n\n“A flat-rate UBI may not replace those professional incomes, raising difficult questions around adequacy and incentives.\n\n“Confidence and consumption could drop. Or, optimistically, AI could redefine human expertise, creating better jobs for middle-skill workers.”\n\n**Affordabi",
      "stance": "neutral"
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. different perspectives",
      "source_url": "https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/",
      "snippet": "UBI gives workers the economic power to say no to poverty wages and poor working conditions. In doing so, it prevents automation from threatening one's...",
      "body_markdown": "In recent years, one of the most popular arguments for universal basic income (UBI) – a regular cash payment delivered to every individual with no work requirement or means test – has been the fear that automation and artificial intelligence (AI) will someday take our jobs. This, it’s argued, will cause massive unemployment and make UBI a virtual necessity – so we better get cracking.\n\nThis argument is often portrayed as *the* automation argument for UBI, but I don’t think it’s the best way to talk about the relationship between automation and UBI. It’s too future-oriented. It’s about what *might* happen *someday*. Even if the argument is ultimately correct, not everyone is convinced the robot-employment apocalypse will happen any time soon. This future-oriented argument gives people permission to say, “OK, call me when someday comes; until then, let’s forget about UBI.”\n\nI believe UBI is long overdue. The argument for it has to be based on the here-and-now, and automation *does* have an important part to play in that argument. Here’s how.\n\n## Automation disrupts people’s lives\n\nWhen we think about technological progress, we might imagine a farmer’s children happily leaving home for better opportunities in the automobile industry. Their grandchildren in turn happily go to school to get even better jobs in the computer industry. But it doesn’t always – or even usually – happen in such a benevolent way.\n\nEven if the total number of jobs increases with automation, innovation disrupts the labour market. People are not interchangeable parts. They spend their lives building up skills, they take a job, and then suddenly their skills have no market value. That’s called technological displacement. Even if it does not lead to permanent technological unemployment, it’s traumatic for workers and their families. Their children or grandchildren might eventually get better jobs, but this is cold comfort to people spending the rest of their lives at the bottom of the labour market.\n\nEven the lucky ones who manage to claw their way back to a comparable job often go through a period of significant suffering. Sometimes they lose their homes or have to declare bankruptcy. Their children suffer along with them. Even a temporary period of poverty can scar children for life.\n\nUBI gives workers the economic power to say no to poverty wages and poor working conditions\n\nYou can see technological displacement in the gates of closed coal mines, the empty factories of hollowed out post-industrial towns, and the vacant storefronts devastated by big box retailers, who were in turn devastated by online retailers later on.\n\nNo one has the right to keep a job forever. Just because you’re a skilled coal miner, truck driver, or university professor doesn’t mean that society has to keep paying you to do that job for the rest of your life. Equally, there is no way to have technological advancement without displacing some workers and some business owners.\n\nBut we don’t need to be so c",
      "stance": "neutral"
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. different perspectives",
      "source_url": "https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/",
      "snippet": "A pretty robust finding in the research is that giving people unconditional cash grants leads them to work less and even stop working at all if the be...",
      "body_markdown": "# Elon Musk's Mistaken Call for a 'Universal High Income'\n\n## AI will not create a jobless dystopia. Paying people a lot of money not to work would.\n\nEven before artificial intelligence was a meaningful force in the economy, technologists, politicians, and policy wonks of all political persuasions have endorsed a universal basic income to cope with the mass unemployment that will be caused by the AI revolution.\n\nThe familiar case is that an AI-powered economy will be able to automate most economic production, making the economy as a whole much richer, but leaving the average person jobless and destitute. The solution is then to redistribute some of the gains from AI to the public by sending everyone, regardless of income, a check.\n\nBusinessman Elon Musk has gone one step further by calling for a \"universal HIGH INCOME\" to pay for the AI-induced unemployment, which he suggested would be inflation-free thanks to the downward pressure AI will put on prices.\n\nUniversal HIGH INCOME via checks issued by the Federal government is the best way to deal with unemployment caused by AI.\n\nAI/robotics will produce goods & services far in excess of the increase in the money supply, so there will not be inflation.\n\n— Elon Musk (@elonmusk) April 17, 2026\n\n\nMusk is almost certainly right that AI will put downward pressure on prices, as one would expect of any productivity-enhancing technology.\n\nHe's mistaken in believing that this makes a universal income (regardless of whether it's basic or \"HIGH\") a wise policy.\n\nEven in a future in which AI does revolutionize the economy, we will not see technologically driven mass unemployment. In fact, a universal basic income would likely result in more of the joblessness it's meant to mitigate.\n\nTo the first point, the industrial revolution has been outsourcing more and more tasks to labor-saving machines for roughly 300 years now. While this ongoing process has certainly made lots of individual jobs obsolete, it has not made jobs generally obsolete.\n\nExcepting the monthly ups and downs of the unemployment rate, the total number of jobs in the economy continues to rise precipitously in the long run.\n\nIf labor-saving technology destroyed the need for labor, we should have *fewer *jobs today than ever before. We don't. Even as farms and factories employ fewer people, we keep finding ways to keep ourselves busy.\n\nThe AI boosters and doomers argue that this time will be different, because unlike spinning jennies, combine harvesters, and email, AI will eventually be smarter than humans at everything. When there's nothing that flesh-and-blood humans can do better than machines, we'll end up doing nothing at all.\n\nThese arguments are obviously speculative because we don't have artificial general intelligence yet. Even when we do, it's reasonable to assume that humans will continue to have employable comparative advantages, if only because humans prize human interaction.\n\nThere are lots of jobs today that could be automated but are",
      "stance": "neutral"
    },
    {
      "question": "Universal Basic Income is the correct policy response to job displacement caused by AI. different perspectives",
      "source_url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
      "snippet": "The Leeds woollen workers voiced precisely the same fears over two centuries ago. The Industrial Revolution did disrupt their livelihoods and did lead...",
      "body_markdown": "Adam Smith attributed the ability of one man to do the work of many to the ‘invention of a great number of machines which facilitate and abridge labour’. Writing at the dawn of the Industrial Revolution, Smith witnessed one of the most profound reorganisations of human capital in history: the transition to mechanised production. The only comparable shift was humanity’s earlier move from nomadic hunter‑gathering to settled agriculture during the Neolithic Revolution.\n\nUnsurprisingly, this transformation provoked deep anxiety. Contemporaneous sources such as the Leeds Woollen Workers’ Petition of 1786 warned that new machinery would deprive workers of the ‘opportunity of getting a livelihood’. Fears of mass unemployment, immiseration and social collapse accompanied technological progress then just as they do now.\n\nToday, it is artificial intelligence that has revived these concerns. Rapid advances in AI systems have reignited worries about job insecurity to such an extent that **universal basic income** (UBI) has re‑entered **mainstream political debate**. Some policymakers now frame UBI as a necessary response to a looming wave of technological unemployment.\n\nAs reported this week, investment minister Jason Stockwood suggested that UBI could be used to ‘soft‑land’ industries most exposed to AI‑driven disruption, potentially funded through a windfall tax on technology companies. Public opinion appears receptive: YouGov polling indicates that 46% of Britons support some form of UBI.\n\nUnlike existing welfare programmes, UBI is unconditional: it is neither means‑tested nor contingent on employment status. It represents a direct, universal transfer from the state to every individual. No country currently operates a comprehensive UBI system, largely because of its sheer fiscal cost, but some have trialled it, including the Welsh Government from 2022 to 2025. One study estimates that providing an annual UBI of just over £11,000 per person in Britain would require a flat income tax rate of around 45%.\n\nAdvocates increasingly argue that AI changes this calculus, making UBI not only necessary but financially feasible. At present, most economic value – and therefore tax revenue – is generated by human labour. If artificial general intelligence were to dramatically increase output without a corresponding increase in human work, large economic surpluses could, in theory, be generated with far fewer workers. The tax base could then shift away from labour towards non‑labour rents, including returns on capital and highly productive AI systems themselves.\n\nAI could also raise public‑sector productivity, particularly in areas such as healthcare and education, reducing the state’s expenditure burden. In combination, faster productivity growth and a rebalanced tax base may create the fiscal space required for a universal income.\n\nEven on these optimistic assumptions, however, UBI would require the wholesale replacement of the existing welfare system. In contrast to t",
      "stance": "neutral"
    }
  ],
  "source_quality_table": [
    {
      "url": "https://aimultiple.com/ai-job-loss",
      "tier": "unknown",
      "tier_source": "model-prior",
      "rationale": "AIMultiple is a B2B tech research and advisory site covering AI topics; it lacks the editorial stan"
    },
    {
      "url": "https://arxiv.org/pdf/2603.20617",
      "tier": "primary-source",
      "tier_source": "wikipedia-rsp",
      "rationale": "Primary source (government, institution, peer-reviewed journal)."
    },
    {
      "url": "https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract",
      "tier": "unknown",
      "tier_source": "model-prior",
      "rationale": "Baker Tilly is a major international accounting and advisory network, but its website is a corporate"
    },
    {
      "url": "https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/",
      "tier": "reputable-news",
      "tier_source": "model-prior",
      "rationale": "openDemocracy is an independent, editorially governed international media platform known for investi"
    },
    {
      "url": "https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/",
      "tier": "reputable-news",
      "tier_source": "model-prior",
      "rationale": "Reason is an established libertarian magazine with professional journalists, clear editorial standard"
    },
    {
      "url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
      "tier": "unknown",
      "tier_source": "model-prior",
      "rationale": "CapX.co is a UK-based opinion and commentary outlet associated with the Centre for Policy Studies; i"
    }
  ]
}
```

*(Note: `rationale` values are capped at 120 chars by `_compact_quality_entry`. `tweet_context` and `image_evidence` omitted — local test run. The input stances are all `"neutral"` at this point — Stage 4 records stances after reconcile re-stamps them.)*

---

### Output (full ReconciliationOutput, stored in freeze)

```json
{
  "consolidated_findings": {
    "verified_propositions": [],
    "refuted_propositions": [],
    "disputed_propositions": [],
    "unaddressed_propositions": [
      {
        "proposition": "Universal Basic Income is the correct policy response to job displacement caused by AI.",
        "reason": "evidence retrieved but silent",
        "is_central": true
      }
    ],
    "contextual_findings": [],
    "challenged_propositions": [],
    "perspectives": [
      {
        "label": "Pro-UBI",
        "summary": "UBI gives displaced workers a floor and bargaining power; Finland/Netherlands pilots showed wellbeing gains even when employment effects were modest.",
        "citing_sources": [
          {
            "url": "https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/",
            "tier": "reputable-news"
          },
          {
            "url": "https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract",
            "tier": "unknown"
          }
        ]
      },
      {
        "label": "Market-adaptation view",
        "summary": "Tech has always created new jobs; UBI research shows it reduces work participation and the policy faces prohibitive fiscal costs (~45% flat tax in UK).",
        "citing_sources": [
          {
            "url": "https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/",
            "tier": "reputable-news"
          },
          {
            "url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
            "tier": "unknown"
          }
        ]
      },
      {
        "label": "Reskilling alternative",
        "summary": "Targeted retraining and graduated safety nets better match displaced workers' varied needs; flat-rate UBI is inadequate for high-skill professional incomes.",
        "citing_sources": [
          {
            "url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
            "tier": "unknown"
          },
          {
            "url": "https://www.bakertilly.global/insights/ai-job-displacement-and-the-case-for-a-new-social-contract",
            "tier": "unknown"
          }
        ]
      }
    ]
  },
  "presentation_payload": {
    "headline_finding": "Experts are sharply divided on whether UBI or targeted retraining better addresses AI-driven job displacement.",
    "counter_fact": null,
    "primary_sources_to_cite": [
      {
        "url": "https://www.opendemocracy.net/en/beyond-trafficking-and-slavery/why-wait-until-the-robots-take-our-jobs-we-need-a-basic-income-now/",
        "display_name": "openDemocracy"
      },
      {
        "url": "https://reason.com/2026/04/17/elon-musks-mistaken-call-for-a-universal-high-income/",
        "display_name": "Reason"
      },
      {
        "url": "https://capx.co/ai-and-jobs-the-case-against-universal-basic-income",
        "display_name": "CapX"
      }
    ],
    "load_bearing_evidence_snippet": "",
    "context_note": null,
    "counterpoints": [],
    "perspectives": [
      {
        "label": "Pro-UBI",
        "summary": "UBI gives displaced workers a floor and bargaining power; Finland/Netherlands pilots showed wellbeing gains even when employment effects were modest.",
        "citing_sources": [
          {"url": "https://www.opendemocracy.net/...", "tier": "reputable-news"},
          {"url": "https://www.bakertilly.global/...", "tier": "unknown"}
        ]
      },
      {
        "label": "Market-adaptation view",
        "summary": "Tech has always created new jobs; UBI research shows it reduces work participation and the policy faces prohibitive fiscal costs (~45% flat tax in UK).",
        "citing_sources": [
          {"url": "https://reason.com/...", "tier": "reputable-news"},
          {"url": "https://capx.co/...", "tier": "unknown"}
        ]
      },
      {
        "label": "Reskilling alternative",
        "summary": "Targeted retraining and graduated safety nets better match displaced workers' varied needs; flat-rate UBI is inadequate for high-skill professional incomes.",
        "citing_sources": [
          {"url": "https://capx.co/...", "tier": "unknown"},
          {"url": "https://www.bakertilly.global/...", "tier": "unknown"}
        ]
      }
    ]
  },
  "tone_neutral_justification": "Reason (libertarian) and openDemocracy (progressive) anchor opposing poles; Baker Tilly and CapX add fiscal and labour-market nuance, ensuring no single ideological framing dominates the summary.",
  "evidence_stances": ["neutral", "neutral", "supports", "supports", "refutes", "refutes"]
}
```

*(Note: `lens_1.narrative` and `lens_1.cross_source_contradictions` are produced in the reconcile call but are not stored in the freeze schema — only `consolidated_findings`, `presentation_payload`, `tone_neutral_justification`, and `evidence_stances` are persisted.)*

---

## Stage 5 — Mechanical audit

**What it does:** Pure Python, < 50 ms. Calls `derive_action_outcome(action, consolidated_findings, source_quality_table)` to re-derive the action outcome from structure alone, then checks it matches what reconcile declared. Also verifies URL containment and the central-proposition invariant.

`derive_action_outcome` for `surface_perspectives` checks: are there ≥ 2 perspectives, each with ≥ 1 source that is `reputable-news`, `fact-checker`, or `primary-source`?

- Perspective "Pro-UBI" → citing_sources includes opendemocracy.net (`reputable-news`) ✓
- Perspective "Market-adaptation view" → citing_sources includes reason.com (`reputable-news`) ✓
- Condition met → outcome = `perspectives_surfaced`

| Check | Result | Detail |
|---|---|---|
| `derive_action_outcome("surface_perspectives", findings, quality_table)` → `perspectives_surfaced` | ✅ pass | ≥2 perspectives each with ≥1 reputable source |
| All cited URLs present in source_quality_table | ✅ pass | 3 `primary_sources_to_cite` all present in the 6-entry quality table |
| Central proposition in exactly one bucket | ✅ pass | c1 in `unaddressed_propositions` (correct for `surface_perspectives`) |

**Audit passed.** `action_outcome = perspectives_surfaced`

---

## Stage 6 — Freeze

**What it does:** Serialises the complete pipeline output to a deep-immutable Pydantic model (`FrozenVerdict`) and writes it to disk at `data/freezes/<invocation_id>.json`. The renderer reads *only* `presentation_payload` and `tone_neutral_justification` — all intermediate reasoning fields are off-limits by contract.

**Written to:** `data/freezes/5ab21772-cfd6-4e09-8f9c-9d07767bff8f.json`

**Top-level fields in the freeze JSON:**

```json
{
  "invocation_id": "5ab21772-cfd6-4e09-8f9c-9d07767bff8f",
  "target_tweet_id": "",
  "invocation_time": "2026-05-26T07:44:34.776842Z",
  "thread_context": "",
  "modality": "text",
  "backend_version": {
    "model": "claude-via-azure-ai-services",
    "search_provider": "claude-web-search:claude-sonnet-4-6",
    "pipeline_commit": "3b57f60513f908e856b79c11c1e0c1469eca20bf"
  },
  "action": "surface_perspectives",
  "action_source": "inferred",
  "pivoted_from": null,
  "invoker_instruction_text": null,
  "action_outcome": "perspectives_surfaced",
  "verdict_label": "NotEnoughEvidence",
  "overall_state": "checked",
  "frozen": true
}
```

*(`verdict_label = "NotEnoughEvidence"` is the legacy 4-value field — retained for backwards compat. `action_outcome = "perspectives_surfaced"` is the current 11-value field that the renderer and all new code reads.)*

---

## Stage 7 — Render

**What it does:** Composes one system prompt from three pieces:

```
system = _ACTION_TEMPLATES[action] + _TONE_REGISTERS[tone] + _hard_constraints_for(action, state, pivoted)
```

5 action templates × 3 tone registers = 15 compositional combinations. Calls Claude, runs `_enforce_invariance` (rejects any URL in body, checks ≤ 256 X-weighted chars, checks not a refusal), retries up to 3× with failure feedback. The poster then appends a t.co-shortened `/info` link — all source URLs live there, not in the tweet body.

**Not invoked** — this was a `run_local.py` test run; render is called by the streamer on real mentions only.

---

### Composed system prompt (what would have been passed for `action=surface_perspectives`, `tone=neutral`, `state=actionable`, `pivoted=False`)

Three pieces concatenated with `\n\n`:

**Piece 1 — Action template (`_SURFACE_PERSPECTIVES_TEMPLATE`):**

```
You are a fact-check bot writing ONE reply tweet to SURFACE MULTIPLE PERSPECTIVES on a contested topic.

INPUT: RendererView. `presentation_payload.perspectives` lists 2–3 credible viewpoints, each with `label`, `summary`, and `citing_sources`. The first two are the strongest.

YOUR JOB BY STATE:
- state="actionable": surface EXACTLY TWO perspectives in tension — the first two in the list. Preserve each label's framing (you may paraphrase if the verbatim form doesn't fit the char budget, but don't switch sides). DO NOT take a side — frame each viewpoint in its own terms. Source naming is OPTIONAL — the appended /info link carries every source already, so name them only if space allows; otherwise let the labels and substance do the work.
- state="no_evidence": acknowledge the topic is contested but credible perspectives weren't surfaced.

A separate `/info` short link is appended automatically; that page lists every perspective with its source URLs. DO NOT include any URL in your reply body.

STRICT NEUTRALITY: every perspective gets the same charitable framing. Don't editorialize about which side is "right".

STYLE EXAMPLES:
- "Two camps: Pro-UBI sees a universal floor as essential; targeted-policy camp prefers retraining + sector subsidies. Both have credible backing. [expand with evidence, stakes, and why each side holds the view — use the full space the content warrants]"
- "Contested space — cost-control view warns of expense; safety-net view emphasizes scale of displacement. Neither side is fringe. [expand: name specific studies, outlets, or experts that anchor each camp, and explain the empirical disagreement]"
```

**Piece 2 — Tone register (`_NEUTRAL_REGISTER`):**

```
REGISTER — plain correction with source. Bode, Vraga & Tully (2020) style: straightforward, evidence-first, detached. Declarative sentences, named sources, no rhetorical flourish.
```

**Piece 3 — Hard constraints (`_hard_constraints_for("surface_perspectives", "actionable", False)`):**

```
HARD CONSTRAINTS (violations are rejected and retried):
- Communicate the headline_finding faithfully.
- Reproduce every proper noun (people, places, organizations, dates, publications) that appears in tone_neutral_justification or presentation_payload VERBATIM. Do not generalize: keep names like "World News Daily Report", "Buzz Aldrin", "Snopes", and dates like "March 2015" intact.
- ZERO URLs in your reply body. The runtime appends a separate /info short link that carries all source URLs + structured reasoning. Name sources by their display_name (e.g. "Snopes", "AP News") in your text — never as a link.
- Never introduce facts outside presentation_payload + tone_neutral_justification.
- No emojis, no hashtags, no @-mentions.
- ≤24976 X-weighted chars total. Use as much space as the content warrants — be detailed, cite evidence, explain reasoning. Don't pad, but don't compress either.
- Output a JSON object with a single "text" field. No preamble, no prose around the JSON.
- This is a surface_perspectives action — present EXACTLY two perspectives (the first two listed). Preserve each label's framing; paraphrase only if the verbatim form blows the char budget. Source naming is OPTIONAL — /info carries them.
```

---

### User prompt (what would have been passed by `_build_prompt(view, "actionable")`)

```
Render the reply.

action: surface_perspectives
action_outcome: perspectives_surfaced
state: actionable

presentation_payload:
{
  "headline_finding": "Experts are sharply divided on whether UBI or targeted retraining better addresses AI-driven job displacement.",
  "counter_fact": null,
  "primary_sources_to_cite": [
    {"url": "https://www.opendemocracy.net/...", "display_name": "openDemocracy"},
    {"url": "https://reason.com/...", "display_name": "Reason"},
    {"url": "https://capx.co/...", "display_name": "CapX"}
  ],
  "load_bearing_evidence_snippet": "",
  "context_note": null,
  "counterpoints": [],
  "perspectives": [
    {
      "label": "Pro-UBI",
      "summary": "UBI gives displaced workers a floor and bargaining power; Finland/Netherlands pilots showed wellbeing gains even when employment effects were modest.",
      "citing_sources": [{"url": "https://www.opendemocracy.net/...", "tier": "reputable-news"}, {"url": "https://www.bakertilly.global/...", "tier": "unknown"}]
    },
    {
      "label": "Market-adaptation view",
      "summary": "Tech has always created new jobs; UBI research shows it reduces work participation and the policy faces prohibitive fiscal costs (~45% flat tax in UK).",
      "citing_sources": [{"url": "https://reason.com/...", "tier": "reputable-news"}, {"url": "https://capx.co/...", "tier": "unknown"}]
    }
  ]
}

tone_neutral_justification:
Reason (libertarian) and openDemocracy (progressive) anchor opposing poles; Baker Tilly and CapX add fiscal and labour-market nuance, ensuring no single ideological framing dominates the summary.
```

---

### Output (not captured — local test run)

What a `tone=neutral` render would produce given this input:

> Two camps on UBI as response to AI-driven job displacement: Pro-UBI (openDemocracy) argues it restores displaced workers' bargaining power — Finland/Netherlands pilots showed wellbeing gains even when employment effects were modest. Market-adaptation view (Reason) argues 300 years of automation have always created new jobs; unconditional cash reduces work participation and a UK UBI would require a ~45% flat income tax. Both sides have credible backing.

*(Illustrative — not the actual model output. Actual render only runs on real mentions via the streamer. Reply body ≤ 256 X-weighted chars; t.co /info link appended separately by poster.)*

---

## Summary

| Stage | LLM | Key inputs | Key output |
|---|---|---|---|
| 1 — Fetch | X API v2 | tweet ID | `tweet_context` (skipped — local run) |
| 1.5 — Multimodal | Claude VLM | images | `image_evidence` (skipped — no images) |
| 2+3 — Extract | Claude Sonnet 4.6, low thinking | tweet_text, invoker_instruction | `action=surface_perspectives`, 2 claims |
| 4a — Seeds | Claude + web_search_20250305 | 2 seed query strings | 6 Evidence records with body_markdown |
| 4b — Controller | Claude Sonnet 4.6, low thinking | full history (6 hits) | `action=stop` |
| 4.5.2 — Tiers | Claude model-prior (L3) | 6 URLs | 2 reputable-news, 1 primary-source, 3 unknown |
| 4.5 — Reconcile | Claude Sonnet 4.6, medium thinking | evidence + body_markdown + tiers | 3 perspectives, stances, headline_finding |
| 5 — Audit | pure Python | consolidated_findings, quality_table | `perspectives_surfaced` confirmed |
| 6 — Freeze | Pydantic serializer | full pipeline state | `5ab21772.json`, 33 KB |
| 7 — Render | Claude Sonnet 4.6, no thinking | presentation_payload + tone_neutral_justification | not invoked (local run) |
