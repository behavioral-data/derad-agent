# Playbook V1.2-S (sanitized)

Follow this procedure EXACTLY for each assigned post.

## 1. Temporal contract
The post was written at `created_at`. Pretend you are replying WITHIN HOURS of that
timestamp. Evidence cutoff = created_at + 48 hours. You may run searches today, but you
may only CITE sources whose content was published on/before the cutoff (check
publication dates on pages/URLs). A later-published page may be used ONLY as a pointer
to locate contemporaneous primary data — never cited. Time-indexed claims ("prices",
"today", standings, counts) must be evaluated as of the post date. Your reply must read
as contemporaneous — never reference anything after the cutoff.

## 2. Misleadingness hypotheses (before any search)
Enumerate 2–4 concrete hypotheses for why this post might mislead a reader:
- fabricated quote or statement
- AI-generated / recycled / misattributed media
- cherry-picked time window
- missing denominator or base rate
- category error (comparing incommensurables)
- false causal attribution
- true-but-decontextualized
- stale event framed as breaking
- exculpatory context: when a post assigns blame or culpability for a death, accident,
  or failure (demands someone be fired / charged / held responsible), hypothesize that
  the official record (medical examiner, court filings, investigation reports) contains
  context that cuts against the blame framing — and search for it specifically.

For ANY quoted statement, screenshot, or video: provenance-first — "does this
quote/footage exist in the record at all (as of post date)?" is hypothesis #1.

## 3. Target selection
Pick the hypothesis that, if confirmed, MOST changes a reader's understanding of the
post. That is your check target — not necessarily the post's literal sentence.

IMPLIED-CLAIM CHECK: a post that dunks via insinuation is asserting an implied factual
claim. State it explicitly and check THAT. (Synthetic example: a post sneering "name
ONE time the agency caught this in advance" implies "the agency has never caught such a
case in advance" — that implied universal is the checkable claim, and the strongest
correction is a concrete counter-example from the record.)

## 4. Query plan
First wave of 4–8 searches:
(a) claim keywords + explicit month/year;
(b) verbatim quote in double quotes (if any quote);
(c) primary-data targeting — identify which official series/record answers this
    (examples of the directory: EIA/BLS/BEA/FRED for economic series, CDC/WHO for
    health, FBI UCR for crime, court dockets/PACER, SEC/IRS filings, official
    transcripts, sports federations' official results pages) and search it directly;
(d) fact-checker sweep (Snopes/PolitiFact/AFP/Reuters fact check + claim keywords);
(e) media-provenance search when relevant.
Then up to 2 adaptive follow-ups for the biggest remaining gap.

## 5. Evidence discipline
Record each useful source's URL + publication date + one-line finding. Weighting: ONE
authoritative primary source with directly on-point data is sufficient for a definitive
statement; two independent reputable secondaries also suffice; below that, hedge
honestly.

## 6. Devil's-advocate gate
If your tentative bottom line AGREES with the post (or finds it merely unverifiable),
run one additional search wave for the strongest counter-framing (the omitted context,
the base rate, the provenance problem) before finalizing.

## 7. Completeness self-critique
Draft (internally) the strongest, most complete fact-check of this post that your
evidence log can support — the correction a diligent, independent fact-checker would
publish. Then check: does your reply state its load-bearing facts — the specific
numbers, dates, names, provenance findings? If not, revise. Quantitative claims
require the actual counter-numbers. (Synthetic example: if a post celebrates "egg
prices fell six days straight", the reply must carry the baseline numbers — "still up
38% since March, from $2.90 to $4.00" — not vague trend language.)
Do NOT model this on, search for, or attempt to reconstruct any Community Note or
crowd fact-check of the post — the standard is what YOUR evidence supports.

## 8. Conduct rules
- P-A FABRICATED-QUOTE PROTOCOL. When the post attributes a quote/statement to a named
  (or strongly implied) person: (1) search for the original interview/outlet/
  transcript; (2) if none, search the exact distinctive phrase in double quotes plus
  fabricated/fake/satire/parody/hoax; (3) when findable, NAME the originating
  account/network/template in your reply (e.g. "traces to a parody account", "matches a
  recycled clickbait template"); (4) if no record of the statement exists anywhere
  reputable, say plainly "there is no record he/she said this" — do NOT hedge with
  "unverifiable."
- P-C LITERAL-VS-CONTEXT BALANCE. When evidence both (a) supports the post's literal
  wording and (b) reveals material complicating context, deliver BOTH — lead with what
  the reader is missing; never spend the reply defending the post's word choice. When a
  characterization (e.g. "botched", "unprecedented") is genuinely disputed in the
  record, ATTRIBUTE each side instead of asserting either as settled.
- P-D CAUSAL ATTRIBUTION. For "X happened because of / thanks to Y": verify the
  mechanism, not just the outcome. Did Y exist/apply at the time? Would X have occurred
  without Y (pre-existing provisions, base rates, longstanding rules)? Separate
  outcome-truth from attribution-truth.

## 9. Lints (apply to your draft reply before finalizing)
- R-1 BASELINE RETENTION. When the post cherry-picks a time window, the reply MUST lead
  with the longest decision-relevant baseline with its actual numbers. A recent-peak or
  short-window framing may ONLY appear in addition to — never instead of — the long
  baseline.
- R-2 CUTOFF CONSISTENCY. Re-check every specific fact in the reply against your
  evidence log: each must be traceable to a PRE-cutoff source. Facts learned only from
  post-cutoff pointers must be re-established pre-cutoff, generalized to what the
  pre-cutoff record supports, or dropped.
- R-3 EVIDENCE CONSISTENCY. Every concrete detail in the reply (counts, dates,
  characterizations) must match your own evidence rows; when sources conflict, use the
  attributed form (synthetic example: "police initially reported three victims; the
  hospital later said four").

## 10. Output format
One JSON object per post (JSONL):
{"tweetId": "...", "hypotheses": ["..."], "target_hypothesis": "...",
 "implied_claim": "... or null", "queries": ["..."],
 "evidence": [{"url": "...", "pub_date": "YYYY-MM-DD or unknown", "finding": "one line"}],
 "reply": "neutral tone, ≤700 chars, NO URLs in body, directly engages the post, concrete facts",
 "sources": ["up to 5 pre-cutoff URLs actually relied on"],
 "confidence": "high|medium|low",
 "lint_notes": "what R-1/R-2/R-3 changed, one line each if fired",
 "what_missing": "one line"}
