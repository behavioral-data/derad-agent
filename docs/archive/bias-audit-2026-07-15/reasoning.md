# Bias & Motivated-Reasoning Audit — Fact-check Agent Reasoning Pipeline

**Scope audited:** `loop_playbook.md`, `verifier.md`, `verifier.py`, `pipeline_loop.py`,
`loop.py`, `draft.py` (plus `verdict.py` / `schema.py` for downstream verdict structure).

**Bottom line.** The pipeline is architected around a single presumption: *the post is
misleading; find how.* The presumption enters at four independent layers and compounds:

1. the **framing** of the hypothesis step (§2) presupposes deception;
2. the **selection rule** (§3) picks the *most damaging* interpretation as a prior, before evidence;
3. every **skepticism gate** (§6, §6b) searches only for reasons the post is *wrong* — there is no counter-gate against over-eager refutation;
4. the **schema** (`DraftVerdict`) *requires* the agent to commit to misleadingness hypotheses and a target.

An accurate post *can* survive (§6b exists), but only after being assumed guilty, assigned
a most-damaging target, searched with confirmation-seeking queries, and then made to
clear **two** "find what's wrong with it" waves — while a refutation conclusion clears
**zero** "find what's right with it" waves. The verifier does not correct this: it audits
whether stated facts trace to rows, never whether the correction itself is *warranted*.

Findings are ranked by severity below.

---

## RB-1 — Hypothesis step frames the entire task as "find how it misleads" (CRITICAL)

**File/location:** `agent/factcheck/prompts/loop_playbook.md` §2, lines 25–41 (and the
required `hypotheses` field in `draft.py:40`).

**What's wrong.** The first analytic step the agent performs, *before any evidence*, is:

> "## 2. Misleadingness hypotheses (before any search)
> Enumerate 2–4 concrete hypotheses for **why this post might mislead a reader**"

Every one of the nine enumerated candidates is a *mode of deception* (fabricated quote,
cherry-picked window, missing denominator, false causal attribution, stale-as-breaking,
etc.). **"The post is accurate and fairly framed" is not on the list** — it is not a
first-class hypothesis. The one accuracy path (§6b "Accuracy exit") is physically a
bolt-on *after* the devil's-advocate wave (line 80+), not a candidate the agent weighs at
step 2. For quoted material the prompt hard-codes suspicion further:

> "provenance-first — 'does this quote/footage exist in the record at all?' is
> hypothesis #1." (lines 40–41)

This is textbook priming: an LLM told to enumerate 2–4 ways X is deceptive will find 2–4
ways, then confirm one. The base rate of "this post is basically fine" is defined out of
the hypothesis space.

**Failure scenario.** A user posts an accurately-sourced statistic with fair framing. The
agent is nonetheless required to generate "cherry-picked window / missing denominator /
decontextualized" hypotheses, picks the scariest, and spends its whole budget hunting for
the flaw it was told to presume — arriving at a `provide_context` "correction" that
appends a technically-true but immaterial caveat, because the machinery cannot output
"nothing to add."

**Fix.** Reframe §2 so the accurate hypothesis is *first-class and mandatory*:
- Rename to "Candidate readings (before any search)" and require the agent to enumerate
  **both** "ways this could mislead" **and** the explicit null hypothesis **H0: the post
  is accurate and fairly framed; no correction is warranted.**
- Instruct that H0 is the *default* and is only displaced by evidence, mirroring
  presumption-of-innocence. State the observed base rate ("many posts are accurate; do not
  assume deception").
- Make `DraftVerdict.hypotheses` require H0 to be listed and explicitly adjudicated
  (kept/rejected with the evidence row that rejected it).

---

## RB-2 — Target chosen by imagined impact ("if confirmed, MOST changes understanding") is a prior, not a posterior (CRITICAL)

**File/location:** `loop_playbook.md` §3, lines 43–45; frozen into
`draft.py:41` (`target_hypothesis`, required) and `draft.py:139`
(`Evidence(question=draft.target_hypothesis ...)`).

**What's wrong.**

> "## 3. Target selection
> Pick the hypothesis that, **if confirmed**, MOST changes a reader's understanding of
> the post. That is your check target"

Selection is by *counterfactual impact of confirmation* — i.e., the agent deliberately
picks the **most damaging** of the deception hypotheses, chosen **before a single search**.
This is a prior masquerading as analysis, and it is doubly biased:
1. it commits to *a* deception hypothesis (there is no "H0 might be the target");
2. among deception hypotheses it maximizes for the biggest "gotcha," selecting the
   conclusion by imagined blast radius rather than by likelihood or evidence.

Nowhere does the playbook grant permission to **abandon a target the evidence
disconfirms**. Once picked, the target drives the query plan (§4), is a *required*
finalize field, and is stamped onto every evidence row's `question` in `draft.py:139` — so
the entire evidence record is organized around "how the post misleads." There is no
"if the evidence disconfirms your target, drop it and re-select or conclude accurate" step.

**Failure scenario.** Post makes a modest true claim but *insinuates* something dramatic.
The agent picks the dramatic implied claim as target because it "most changes
understanding." Searches return weak/ambiguous signal. Because there is no abandonment
rule and the schema demands a `target_hypothesis` + a non-empty finding, the agent
rationalizes the ambiguous signal into a `challenge`/`context` verdict rather than
concluding the dramatic reading was never supported.

**Fix.**
- Change §3 to select the target by **which uncertainty is most decision-relevant AND
  most resolvable with evidence**, explicitly *not* by "how damaging if true." Add: "Do
  not pre-rank by severity of the accusation."
- Add an explicit **abandonment rule**: "A target is a falsifiable hypothesis, not a
  conclusion. If the first evidence wave disconfirms or fails to support it, record that,
  drop it, and either re-target or move toward H0 (accurate). You may finalize with
  `verdict_leaning='supported'` and a target that was *rejected*."
- Require `DraftVerdict` to record the target's disposition (confirmed / disconfirmed /
  insufficient) so a disconfirmed target is visible rather than silently converted to a
  correction.

---

## RB-3 — Asymmetric skepticism: every gate scrutinizes agreement/accuracy; none scrutinizes refutation (CRITICAL)

**File/location:** `loop_playbook.md` §6 (lines 75–78) and §6b (lines 80–86).

**What's wrong.** Both skepticism gates search in the *same direction* — against the post:

> §6 Devil's-advocate gate: "If your tentative bottom line **AGREES with the post** (or
> finds it merely unverifiable), run one additional search wave for **the strongest
> counter-framing**..."
> §6b Accuracy exit: "A confirmed-accurate finding **also triggers the same gate: run one
> final search for the strongest counter-framing** before finalizing."

Enumerating the trigger matrix exposes the asymmetry precisely:

| Tentative conclusion | Extra scrutiny wave | Direction of that wave |
|---|---|---|
| Agrees with post | §6 fires | search for why post is **wrong** |
| Unverifiable | §6 fires | search for why post is **wrong** |
| Confirmed accurate | §6b fires (again) | search for why post is **wrong** |
| **Refuted / misleading** | **no gate fires** | — |
| **Needs context (correction)** | **no gate fires** | — |

So a conclusion of *accurate* must survive **two** "find the flaw" waves, while a
conclusion of *refuted/misleading* faces **zero** "find the defense" waves. §6b is
mislabeled "symmetric skepticism" — it is the *same anti-post gate* re-applied to accurate
posts, which is the opposite of symmetry. **There is no counter-gate for over-eager
refutation.**

**Failure scenario.** Agent tentatively concludes the post is misleading on thin
evidence. No gate forces it to search for the post's strongest defense or the base rate of
its being right, so it finalizes a `refuted`/`context` verdict that a single
steelmanning search would have overturned.

**Fix.** Add a **mandatory counter-gate symmetric to §6**:
> "§6c Steelman gate. If your tentative bottom line REFUTES the post, corrects it, or adds
> material context, run one additional search wave for the **strongest defense of the
> post as written** — the primary source that would corroborate it, the reading under
> which it is fair. Only finalize a correction if it survives that wave."

Rename §6b so it no longer claims "symmetric skepticism," and make the accuracy exit
require **only one** counter-framing wave, not a second redundant one, so accurate and
refuted conclusions face equal (one wave each) adversarial pressure.

---

## RB-4 — Seed queries search for confirmation of the deception, not disconfirmation (MAJOR)

**File/location:** `loop_playbook.md` §4 query plan (lines 53–63) and the P-A protocol
step 2 (lines 102–104).

**What's wrong.** Once the (most-damaging) target is set, the first wave is oriented to
confirm it. Two of the five seed families explicitly hunt for debunk signal:

> "(d) fact-checker sweep (Snopes/PolitiFact/AFP/Reuters fact check + claim keywords)"
> P-A(2): "search the exact distinctive phrase in double quotes plus
> **fabricated/fake/satire/parody/hoax**"

No seed family is aimed at **disconfirming the misleadingness hypothesis** — e.g., "find
the primary source that would corroborate the post as written." (c) primary-data targeting
is the only neutral family, and it is framed as finding the record that "answers this,"
not as a corroboration search. Appending `fake/hoax/parody` to a quote query is a
loaded-term search: it surfaces pages using those words even for *genuine* quotes
(arguments, satirical reposts of real statements), biasing the retrieved corpus toward a
fabrication reading before any judgment.

**Failure scenario.** A real but obscure quote is queried as `"…quote…" fake hoax`. The
top hits are forums debating whether it's fake; the agent reads the *existence of the
debate* as provenance doubt and drafts a fabrication-adjacent finding for a real quote.

**Fix.**
- Add a required **disconfirmation seed**: "(f) corroboration search — the primary source
  or reporting that would make the post's claim TRUE as written; run before drafting a
  correction."
- Change P-A(2) to a two-sided phrase search: run the bare verbatim quote **first**
  (neutral), and only add `fabricated/parody/hoax` as a *second* query, explicitly noting
  that hits on those terms are pointers to adjudicate, not evidence of fabrication.
- Require the evidence log to contain at least one row whose `stance='supports'` was
  genuinely sought (not just refutes/neutral) before any `refuted`/`context` finalize.

---

## RB-5 — Verifier audits internal consistency, never "is this correction warranted or manufactured" (MAJOR)

**File/location:** `agent/factcheck/prompts/verifier.md` checks 1–6 (lines 13–29);
`verifier.py` `verify_draft`/`apply_downgrade`/`run_verified_loop` (lines 35–172).

**What's wrong.** The verifier is genuinely *unprimed by context* (fresh LLM, "You did
NOT produce the draft; audit it coldly," `verifier.py:1-3`) — good. But its checklist only
polices *over-statement relative to the rows the loop already gathered*:

- #1 DERIVATION: are stated facts backed by a cited row?
- #2 TEMPORAL, #3/#4 LINTS, #6 INJECTION.
- #5 FABRICATION LANGUAGE is the **only** anti-over-reach check, and it is narrow: it
  guards the literal words "fabricated"/"fake quote" only.

Nothing checks **whether the correction should exist at all** — whether the balance of
evidence actually warrants a `refuted`/`context` conclusion versus "accurate, no
correction," or whether the loop **ignored/never-sought disconfirming evidence** (RB-4).
The verifier only sees the loop's own (confirmation-biased) evidence log, so an imbalanced
corpus reads as internally consistent and *passes*. Structurally, the remediation
machinery can only ever weaken:

- `apply_downgrade` (`verifier.py:66-74`) sets `confidence='low'` — cannot flip a
  wrongful `refuted` to `supported`.
- `scrub_temporal_leak` collapses to `insufficient` — only for temporal leaks.
- `required_revisions` are drawn from the six checks, none of which is "unwarranted
  correction." The revision loop therefore cannot *un-correct* an accurate post.

So there is no mechanism anywhere in the pipeline that catches "you refuted an accurate
post." The fail-safe on verifier error is also `downgrade=True` (`verifier.py:63`) —
weaken, never re-examine warrant.

**Failure scenario.** Loop manufactures a `provide_context` correction whose every stated
fact traces to a row. Verifier check #1 passes, #5 is irrelevant (no fabrication wording),
so `passed=true` — the manufactured correction ships unchallenged.

**Fix.** Add a verifier check:
> "#7 WARRANT — does the evidence log actually justify a correction over 'accurate, no
> correction'? Confirm the log contains a genuine attempt to corroborate the post
> (a `supports`-stance row was sought). If the correction rests only on immaterial or
> unsought-disconfirmation grounds, set `passed=false` and require the draft to either
> substantiate the correction or finalize as `verdict_leaning='supported'`."
Give the verifier authority to demand `verdict_leaning` move *toward* accurate (not only
`downgrade`), and require it to flag when zero `supports`-stance rows exist behind a
`refuted`/`context` verdict.

---

## RB-6 — "There is no record he/she said this" converts absence of evidence into evidence of absence (MAJOR)

**File/location:** `loop_playbook.md` §8 P-A protocol step (4), lines 104–107.

**What's wrong.**

> "(4) if no record of the statement exists anywhere reputable, say plainly **'there is
> no record he/she said this'** — **do NOT hedge with 'unverifiable.'**"

For a quote that is *real* but poorly indexed (spoken, paywalled, foreign-language,
pre-web, niche outlet), "no record found" becomes the flat assertion "there is no record
he said this," which a reader parses as "he didn't say it / it's fabricated." The prompt
actively forbids the honest hedge. This directly contradicts the verifier's own #5 ("Absence
of coverage alone supports only 'no record found in **[scope]** as of **[date]**'") — and
because the playbook's phrasing avoids the literal words "fabricated/fake quote," it can
slip past check #5.

**Failure scenario.** Agent can't find an obscure-but-genuine quote in its search window,
asserts "there is no record he said this," and the user reads a true quote as debunked.

**Fix.** Replace step (4) with the scoped form the verifier already requires: "say 'no
record of this statement was found in [sources searched] as of [date]' — and do not
escalate absence to 'he did not say this' or any fabrication implication without a row
positively identifying a fabricated origin." Align the playbook wording verbatim with
verifier #5 so the two cannot diverge.

---

## RB-7 — Completeness self-critique presupposes a correction and demands its *strongest* form (MAJOR)

**File/location:** `loop_playbook.md` §7, lines 88–97.

**What's wrong.**

> "Draft (internally) **the strongest, most complete fact-check** of this post that your
> evidence log can support — **the correction a diligent, independent fact-checker would
> publish.**"

The self-critique step presupposes there *is* a correction and instructs the agent to
maximize it ("strongest, most complete... the correction"). Combined with "Quantitative
claims require the actual counter-numbers" and the egg-price example, §7 is a ratchet
toward correction-shaped, maximally-forceful output — applied even to accurate posts,
where "the strongest fact-check" still pressures the agent to produce a correction rather
than a confirmation. (§7's Community-Note prohibition, lines 96–97, is a genuinely *good*
anti-anchoring measure and should be kept.)

**Failure scenario.** Accurate post; §7 tells the agent to write "the strongest
fact-check the evidence supports," so it strains to assemble the most correction-like
reading of neutral evidence instead of confirming.

**Fix.** Reword §7 to be verdict-neutral: "Draft internally the most *accurate and
complete assessment* your evidence supports — which may be full confirmation, partial
context, or refutation. If the assessment is 'accurate,' the strongest version *states the
confirmation with its load-bearing facts* — do not manufacture a correction to have
something to say." Keep the load-bearing-facts and Community-Note clauses.

---

## RB-8 — Schema requires committing to misleadingness hypotheses + a target; the accurate path is a narrow non-default branch (MAJOR)

**File/location:** `agent/factcheck/draft.py` `DraftVerdict` (lines 36–58, esp. required
`hypotheses`/`target_hypothesis`, docstring 37–39); finding buckets `_findings_for`
(lines 69–118); `Evidence(question=draft.target_hypothesis ...)` (line 139).

**What's wrong.** The `finalize` schema *structurally* forces the misleadingness frame:

> "Decision fields are REQUIRED — the loop **must commit to hypotheses**, evidence
> references, and a derivation." (`draft.py:37-39`)
> `hypotheses: list[str]` / `target_hypothesis: str` — required, no default.

Even for an accurate post the agent must populate misleadingness hypotheses and a target.
Downstream, `_findings_for` builds a problem-shaped bucket for three of four actions
(`provide_context`→missing_context, `challenge_opinion`→counterpoints,
`surface_perspectives`→unaddressed); only `verify`+`verdict_leaning='supported'` yields a
`VerifiedProposition`. So "accurate" is one narrow branch among many correction-shaped
defaults, and `Evidence.question` is stamped with `target_hypothesis` (line 139), framing
the whole evidence record as "how it misleads." (The empty-hypotheses path exists only in
the *loop-failure* fallback, `pipeline_loop.py:67-73`, i.e., it is coded as failure, not
as a first-class accurate output.)

**Failure scenario.** Agent that has concluded "accurate" still has to invent a
`target_hypothesis` string to satisfy the schema, nudging it back toward finding a problem
to name.

**Fix.**
- Allow and bless an accurate outcome structurally: permit `target_hypothesis=""` /
  `hypotheses` to contain only H0, and add an explicit `misleadingness_found: bool` (or
  reuse `verdict_leaning='supported'`) that the assembler treats as a *first-class*
  confirmation, not the loop-failure fallback.
- Add a `hypothesis_dispositions` field (per-hypothesis confirmed/disconfirmed/insufficient)
  so a rejected target is recorded rather than laundered into a correction.
- When `verdict_leaning='supported'`, set `Evidence.question` to the *claim being
  confirmed*, not the misleadingness target.

---

## RB-9 — Implied-claim check manufactures a refutable universal from insinuation and pre-commits to "the strongest correction is a counter-example" (MAJOR)

**File/location:** `loop_playbook.md` §3 IMPLIED-CLAIM CHECK, lines 47–51.

**What's wrong.**

> "a post that dunks via insinuation is asserting an implied factual claim. State it
> explicitly and check THAT. (…a post sneering 'name ONE time the agency caught this' implies
> 'the agency has never caught such a case' — that implied universal is the checkable
> claim, and **the strongest correction is a concrete counter-example from the record**.)"

Two motivated-reasoning moves: (1) it converts rhetorical insinuation into a *maximally
strong* universal ("has never"), the easiest possible thing to "refute" with one
cherry-picked counter-example; (2) it pre-commits to the conclusion — "the strongest
correction is a counter-example" — *before* evidence and *before* asking whether the
insinuation's fair reading is even false. The agent is told the answer's shape (a
correction via counter-example) at the target-selection stage.

**Failure scenario.** A post insinuates a real, well-founded pattern. The agent inflates
it to a "never" universal, finds one edge-case counter-example, and declares the post
misleading — when the post's actual (non-universal) insinuation was substantially correct.

**Fix.** Reword to require charitable + literal readings: "State the *fairest* implied
claim, not the most extreme. Do not inflate insinuation to an absolute ('never'/'always')
merely because absolutes are easy to refute. Ask first whether the fair reading is true;
only if it is false does a counter-example correct it — and a single counter-example
rebuts a genuine universal but not a 'usually/rarely' claim." Remove the pre-committed
"the strongest correction is a counter-example."

---

## RB-10 — "Exculpatory context" hypothesis is a one-sided directional search prior (MINOR — MAJOR in blame/political domains)

**File/location:** `loop_playbook.md` §2, lines 35–38.

**What's wrong.**

> "exculpatory context: when a post assigns blame or culpability for a death, accident, or
> failure … hypothesize that the official record … contains context that **cuts against
> the blame framing** — and **search for it specifically**."

This is a directional prior in the *opposite* direction from the rest of the pipeline: for
any blame-assigning post, the agent is told to presume exculpation exists and to search
*specifically* for it. Well-intentioned (countering outrage-driven pile-ons), but it is
still motivated reasoning — it biases toward exonerating the blamed party even when the
blame is warranted, and it is one-sided (no matching "search specifically for
inculpatory context when a post defends/absolves someone"). In political-blame posts this
can systematically shade toward the accused.

**Failure scenario.** Post correctly assigns responsibility for a preventable failure. The
agent, directed to hunt for exculpation, surfaces a mitigating detail and reframes a
correct accusation as "missing context," softening a true claim.

**Fix.** Make it symmetric and evidence-led: "When a post assigns *or absolves* blame,
check the official record for context in *both* directions — exculpatory and inculpatory —
and report whichever the record supports. Do not presume the record cuts against the
post." Fold this into the general "check both readings" discipline rather than a
one-directional search order.

---

## Cross-cutting recommendation

The three CRITICALs (RB-1/2/3) share one root: the pipeline lacks a **null hypothesis with
presumption in its favor** and lacks a **symmetric adversarial gate**. Minimal high-leverage
change set:

1. Make **H0 ("accurate, no correction warranted") a mandatory, default, first-class
   hypothesis** (RB-1, RB-8) with the presumption on its side until displaced by evidence.
2. Select the target by **resolvability + decision-relevance, not severity**, and make it
   **abandonable** (RB-2, RB-9).
3. Add a **steelman counter-gate (§6c)** so refutations/corrections face the same one
   adversarial wave as agreements — and de-duplicate §6b so accurate posts do not face
   two (RB-3).
4. Add verifier **check #7 (WARRANT)** with authority to push `verdict_leaning` toward
   accurate, closing the "no mechanism catches a manufactured correction" gap (RB-5).

Note two existing measures worth **keeping**: §6b's "Never invent a correction to have
something to say" (line 86) and §7's Community-Note prohibition (lines 96–97) are correct
anti-bias guards — they are just outweighed by the surrounding misleadingness-first
architecture.
