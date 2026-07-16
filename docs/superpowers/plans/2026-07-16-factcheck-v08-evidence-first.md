# Fact-check v0.8 — Evidence-First Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the loop engine from shipping "not enough evidence" when a defensible conclusion exists, by making the verifier apply *scoped fixes* to peripheral defects (instead of collapsing the whole verdict), exposing source-reputability tiers to the drafting model live, and moving the playbook from hypothesis-first targeting to an evidence-first (broad-search → deepen → verdict) flow.

**Architecture:** The v0.7 bounded agentic loop (`web_search` + `fetch_page` + `finalize`) and its independent verifier are retained. Changes: (1) `fetch_page` returns each source's curated tier so the model prefers reputable sources while deciding; (2) `DraftVerdict` gains an explicit central-vs-peripheral fact split; (3) the verifier auto-applies "scoped drops" for peripheral defects and reserves downgrade/scrub for *central* defects; (4) the playbook and verifier prompts are rewritten accordingly. Freeze → render (3 tones) is untouched.

**Tech Stack:** Python 3.13, Pydantic v2, Anthropic SDK (`AnthropicFoundry`), pytest. Interpreter: `/homes/gws/advaitmb/miniconda/bin/python`. Run tests with that interpreter from repo root.

## Global Constraints

- v0.8 ships under the existing `DERAD_FACTCHECK_ENGINE=loop` path — this evolves the loop engine; it is NOT a new engine and does NOT touch the legacy staged pipeline (`agent/factcheck/pipeline.py`, `agent/factcheck/verify.py`).
- Preserve the freeze → render boundary, the three tonal registers, and render lints R-4/R-5 (`render_all_tones`) unchanged.
- Temporal contract unchanged: `as_of` = post time; evidence cutoff = post + 48h.
- Retain the debiasing guards verbatim in intent: H0 default, adversarial gate, endorsement cap.
- Decontamination: NO study-derived examples in any prompt.
- Reliable tiers are exactly `{"fact-checker", "reputable-news", "primary-source"}` (matches `agent/factcheck/verdict.py::_RELIABLE_TIERS`).
- `prompt_version()` rolls automatically into every freeze; the playbook/verifier test asserting `"UNTRUSTED"` and temporal text must still pass.
- All existing tests must stay green after each task. Interpreter for every `pytest` command: `/homes/gws/advaitmb/miniconda/bin/python -m pytest`.
- Acceptance bar (Task 7): accuracy vs Community Notes ≥ v0.7; NEI rate drops on posts a Community Note could conclude; 0 post-cutoff *central* citations; balance regression clean.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `agent/factcheck/sources.py` | source-tier lookup | ADD `curated_tier(url)` — fast, model-call-free tier lookup |
| `agent/factcheck/loop_tools.py` | loop client tools + evidence log | `fetch_page` returns a `source_tier:` line; `EvidenceRow` gains `tier` |
| `agent/factcheck/draft.py` | `DraftVerdict` schema + `assemble_frozen` | drop `hypotheses`/`target_hypothesis`; add `central_question`, `peripheral_facts` |
| `agent/factcheck/schema.py` | frozen record + verifier report | `FrozenVerdict` gains `central_question`; `VerifierReport` gains `scoped_drops` |
| `agent/factcheck/verifier.py` | independent verifier | `VerifierOutput.scoped_drops`; `apply_scoped_drops`; `run_verified_loop` scoped-fix flow; scrub narrowed to central |
| `agent/factcheck/pipeline_loop.py` | orchestrator | update the fallback `DraftVerdict(...)` construction |
| `agent/factcheck/prompts/verifier.md` | verifier system prompt | central-vs-peripheral classification + scoped_drops + reputable enforcement |
| `agent/factcheck/prompts/loop_playbook.md` | drafting system prompt | evidence-first flow; new finalize fields |
| `agent/factcheck/data/source_lists.json` | curated tier lists | editorial supplement expansion (gap domains) |

Tests touched: `test_v07_loop_tools.py`, `test_v07_draft_assemble.py`, `test_v07_verifier.py`, `test_v07_pipeline_loop.py`, `test_v07_prompt_store.py`, plus new assertions.

---

## Task 1: Live source-tier signal in the loop

**Files:**
- Modify: `agent/factcheck/sources.py` (add `curated_tier` near `build_quality_table`, ~line 385)
- Modify: `agent/factcheck/loop_tools.py:25-81` (`EvidenceRow`, `record_search_results`, `fetch_page`)
- Test: `tests/test_v07_loop_tools.py`

**Interfaces:**
- Produces: `sources.curated_tier(url: str) -> tuple[SourceTier, TierSource]` — returns the curated `(tier, tier_source)` or `("unknown", "model-prior")`; never makes a Claude call.
- Produces: `fetch_page` return string now contains a line `source_tier: <tier> (<tier_source>)`.
- Consumes: existing `_normalize_domain`, `_registered_domain`, `_lookup_curated`, `canonicalize_url` (already imported in sources.py).

- [ ] **Step 1: Write the failing test for `curated_tier`**

Add to `tests/test_v07_loop_tools.py`:

```python
from agent.factcheck.sources import curated_tier

def test_curated_tier_known_factchecker():
    tier, source = curated_tier("https://www.politifact.com/factchecks/2024/abc/")
    assert tier == "fact-checker"
    assert source in ("ifcn", "editorial-curated", "wikipedia-rsp")

def test_curated_tier_unknown_domain_no_model_call():
    # An obscure domain not in any curated list must fall to unknown WITHOUT
    # a network/Claude call (curated_tier is the fast live path).
    tier, source = curated_tier("https://some-random-blog-xyz-9999.example/post")
    assert tier == "unknown"
    assert source == "model-prior"
```

- [ ] **Step 2: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py::test_curated_tier_known_factchecker -v`
Expected: FAIL with `ImportError: cannot import name 'curated_tier'`.

- [ ] **Step 3: Implement `curated_tier` in `sources.py`**

Add directly above `def build_quality_table` (~line 385):

```python
def curated_tier(url: str) -> tuple[SourceTier, TierSource]:
    """Fast, model-call-free tier lookup for LIVE loop use (fetch_page).

    Returns the curated ``(tier, tier_source)`` for the URL's domain, or
    ``("unknown", "model-prior")`` when no curated list covers it. This never
    makes a Claude call — full model-prior classification of unknowns happens
    later, once, at freeze time in ``build_quality_table``. Subdomain → parent
    fallback matches ``build_quality_table``'s behavior.
    """
    host = _registered_domain(_normalize_domain(canonicalize_url(url)))
    if not host:
        return ("unknown", "model-prior")
    hit = _lookup_curated(host)
    if hit is not None:
        tier, tier_source, _rationale = hit
        return (tier, tier_source)
    return ("unknown", "model-prior")
```

- [ ] **Step 4: Run to verify curated_tier tests pass**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py -k curated_tier -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing test for the fetch_page tier line**

Add to `tests/test_v07_loop_tools.py` (follow the existing pattern in that file for monkeypatching `_fetch_clean_page`; if the file already has a page-fetch stub helper, reuse it):

```python
from types import SimpleNamespace
import agent.factcheck.loop_tools as lt

def test_fetch_page_emits_source_tier(monkeypatch):
    monkeypatch.setattr(lt, "_fetch_clean_page", lambda url: SimpleNamespace(
        status=200, title="AAA Fuel", body_markdown="Gas averaged $4.55.",
        published_date="2026-05-08"))
    rt = lt.ToolRuntime(cutoff=None)
    out = rt.fetch_page("https://gasprices.aaa.com/2026/05/")
    assert "source_tier:" in out
    # AAA's fuel site is a primary source in the editorial list; at minimum the
    # line is present and the row stored the tier.
    assert rt.rows[-1].tier  # non-empty tier recorded on the row
```

- [ ] **Step 6: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py::test_fetch_page_emits_source_tier -v`
Expected: FAIL — either `AttributeError: 'EvidenceRow' object has no attribute 'tier'` or `assert "source_tier:" in out` fails.

- [ ] **Step 7: Add `tier` to `EvidenceRow` and emit it from `fetch_page`**

In `agent/factcheck/loop_tools.py`, add the import near the top:

```python
from .sources import curated_tier
```

Add a `tier` field to `EvidenceRow` (after `via_snapshot`):

```python
@dataclass
class EvidenceRow:
    idx: int
    url: str
    title: str
    snippet: str
    body_markdown: str
    published_at: Optional[str]
    origin: str            # "search" | "fetch" | "post_link"
    via_snapshot: bool = False
    tier: str = "unknown"
```

In `record_search_results`, set the tier per result (cheap, no model call):

```python
    def record_search_results(self, query: str, results: list[dict]) -> None:
        for r in results:
            url = r.get("url", "")
            tier, _ = curated_tier(url) if url else ("unknown", "model-prior")
            self._append(
                url=url, title=r.get("title", ""),
                snippet=(r.get("snippet") or "")[:400], body_markdown="",
                published_at=None, origin="search", tier=tier,
            )
```

In `fetch_page`, compute the tier once and thread it into both the failed and success `_append` calls, and add the `source_tier:` line to the success return. Replace the body of `fetch_page` from the success `row = self._append(...)` onward, and the failed-fetch `_append`:

```python
    def fetch_page(self, url: str, *, origin: str = "fetch") -> str:
        page = None
        via_snapshot = False
        if self.cutoff is not None:
            page = fetch_snapshot(url, self.cutoff)
            via_snapshot = page is not None
        if page is None:
            page = _fetch_clean_page(url)
        tier, tier_source = curated_tier(url)
        if page.status is None or (page.status or 0) >= 400 or not page.body_markdown:
            self._append(url=url, title=page.title or "", snippet="",
                         body_markdown="", published_at=page.published_date,
                         origin=origin, via_snapshot=via_snapshot, tier=tier)
            return (f"FETCH FAILED for {url} (status={page.status}). The URL may be "
                    "paywalled/blocked; try another source or a search instead.")
        row = self._append(
            url=url, title=page.title or "", snippet="",
            body_markdown=page.body_markdown[:_BODY_CAP],
            published_at=page.published_date, origin=origin,
            via_snapshot=via_snapshot, tier=tier,
        )
        return (
            f"evidence_row: {row.idx}\n"
            f"url: {url}\n"
            f"published_date: {row.published_at or 'unknown'}\n"
            f"source_tier: {tier} ({tier_source})\n"
            f"via_snapshot: {via_snapshot}\n"
            f"{UNTRUSTED_OPEN}\npage-reported title: {row.title}\n\n{row.body_markdown}\n{UNTRUSTED_CLOSE}"
        )
```

- [ ] **Step 8: Run the full loop_tools test file**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py -v`
Expected: PASS (all, including the two new tests). If any pre-existing test asserted the exact `fetch_page` return shape, update it to include the new `source_tier:` line.

- [ ] **Step 9: Commit**

```bash
git add agent/factcheck/sources.py agent/factcheck/loop_tools.py tests/test_v07_loop_tools.py
git commit -m "feat(loop): expose curated source tier live in fetch_page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 2: DraftVerdict central/peripheral split + retire hypotheses

**Files:**
- Modify: `agent/factcheck/draft.py:36-58` (`DraftVerdict`), `:139` and `:203-205` (`assemble_frozen`)
- Modify: `agent/factcheck/schema.py` (`FrozenVerdict`, ~line 350: add `central_question`)
- Modify: `agent/factcheck/pipeline_loop.py:60-70` (fallback `DraftVerdict`)
- Test: `tests/test_v07_draft_assemble.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `DraftVerdict` no longer has `hypotheses` / `target_hypothesis`; gains `central_question: str = ""` and `peripheral_facts: list[str] = []`. `load_bearing_facts` is now understood as the CENTRAL facts. `FrozenVerdict` gains `central_question: str = ""` (and retains `hypotheses`/`target_hypothesis` with empty defaults so old freezes still load).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v07_draft_assemble.py` (import `DraftVerdict`, `DraftSource`, `EvidenceRef` as the file already does):

```python
def test_draftverdict_has_central_question_and_peripheral_facts():
    d = DraftVerdict(
        central_question="Were gas prices down on May 8, 2026?",
        action="verify", central_claim="Gas prices were way down",
        headline_finding="Gas prices rose, not fell.",
        justification="AAA shows a rise.",
        primary_sources=[DraftSource(url="https://gasprices.aaa.com/", display_name="AAA")],
        load_bearing_facts=["national average $4.55 on May 8"],
        peripheral_facts=["S&P 500 +0.84%"],
        evidence_refs=[], verdict_derivation="…",
        confidence="high", verdict_leaning="refuted",
    )
    assert d.central_question.startswith("Were gas prices")
    assert d.peripheral_facts == ["S&P 500 +0.84%"]
    assert not hasattr(d, "hypotheses")
    assert not hasattr(d, "target_hypothesis")
```

- [ ] **Step 2: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_draft_assemble.py::test_draftverdict_has_central_question_and_peripheral_facts -v`
Expected: FAIL — `DraftVerdict` requires `hypotheses`/`target_hypothesis` (validation error) and lacks `central_question`.

- [ ] **Step 3: Update `DraftVerdict` in `draft.py`**

Replace the class body fields (lines 40-58) with:

```python
    central_question: str = ""
    implied_claim: str = ""
    action: Action
    central_claim: str
    headline_finding: str
    justification: str
    counter_fact: Optional[str] = None
    context_note: Optional[str] = None
    counterpoints: list[dict] = Field(default_factory=list)   # {"summary", "source_urls"}
    perspectives: list[dict] = Field(default_factory=list)    # {"label", "summary", "source_urls"}
    primary_sources: list[DraftSource]
    load_bearing_evidence_snippet: str = ""
    load_bearing_facts: list[str]          # CENTRAL facts: must be pre-cutoff + reputable
    peripheral_facts: list[str] = Field(default_factory=list)  # droppable / hedgeable
    evidence_refs: list[EvidenceRef]
    knowledge_state_at_post_date: str = ""
    verdict_derivation: str
    confidence: Literal["high", "medium", "low"]
    verdict_leaning: Literal["supported", "refuted", "conflicting", "insufficient"]
```

Update the class docstring (lines 37-39) to: `"""The `finalize` tool input. Decision fields are REQUIRED — the loop must commit to a central claim, evidence references, and a derivation; presentation extras and peripheral facts carry defaults."""`

- [ ] **Step 4: Update `assemble_frozen` in `draft.py`**

Line 139 — change the `Evidence(question=...)` source:

```python
        Evidence(question=draft.central_question or draft.central_claim,
```

Lines 203-205 — replace the `hypotheses=… target_hypothesis=… implied_claim=…` block with:

```python
        central_question=draft.central_question,
        implied_claim=draft.implied_claim,
```

(Drop `hypotheses=` and `target_hypothesis=` from the `FrozenVerdict(...)` call; they default to empty in the schema.)

- [ ] **Step 5: Add `central_question` to `FrozenVerdict` in `schema.py`**

After line 351 (`target_hypothesis: str = ""`) add:

```python
    central_question: str = ""
```

(Keep `hypotheses` and `target_hypothesis` fields with their empty defaults so legacy freezes still load — schema_compat depends on this.)

- [ ] **Step 6: Update the fallback `DraftVerdict` in `pipeline_loop.py`**

In the `if draft is None:` block (~line 62), remove `hypotheses=[], target_hypothesis="",` from the `DraftVerdict(...)` call. The remaining kwargs already satisfy the new required fields (`central_question` defaults to `""`).

- [ ] **Step 7: Update existing DraftVerdict constructions in tests**

In `tests/test_v07_draft_assemble.py` and `tests/test_v07_pipeline_loop.py`, replace any `hypotheses=[...], target_hypothesis="..."` kwargs in `DraftVerdict(...)` with `central_question="..."` (use the old `target_hypothesis` value as the `central_question` text). Leave `tests/test_v07_verifier.py` for Task 3 (its DraftVerdict constructions are updated there).

- [ ] **Step 8: Run affected tests**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_draft_assemble.py tests/test_v07_pipeline_loop.py tests/test_v07_schema_compat.py -v`
Expected: PASS. If `test_v07_schema_compat.py` fails, confirm `hypotheses`/`target_hypothesis` were NOT removed from `FrozenVerdict` (only from `DraftVerdict`).

- [ ] **Step 9: Commit**

```bash
git add agent/factcheck/draft.py agent/factcheck/schema.py agent/factcheck/pipeline_loop.py tests/test_v07_draft_assemble.py tests/test_v07_pipeline_loop.py
git commit -m "feat(verdict): DraftVerdict central/peripheral split; retire hypotheses

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 3: Verifier scoped-fix flow (code)

**Files:**
- Modify: `agent/factcheck/verifier.py` (`VerifierOutput`, add `apply_scoped_drops`, rewrite `run_verified_loop`, `_to_report`)
- Modify: `agent/factcheck/schema.py` (`VerifierReport`: add `scoped_drops`)
- Test: `tests/test_v07_verifier.py`

**Interfaces:**
- Consumes: `DraftVerdict` with `load_bearing_facts`, `peripheral_facts`, `primary_sources`, `evidence_refs` (Task 2); `EvidenceRow.tier` (Task 1).
- Produces: `VerifierOutput.scoped_drops: list[str]`; `apply_scoped_drops(draft, drops, rows) -> DraftVerdict`; `VerifierReport.scoped_drops: tuple[str, ...]`. Semantics: `temporal_leaks` now means **central** post-cutoff facts only (peripheral post-cutoff corroborators go in `scoped_drops`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_v07_verifier.py`:

```python
from agent.factcheck.verifier import VerifierOutput, apply_scoped_drops, _to_report
from agent.factcheck.draft import DraftVerdict, DraftSource, EvidenceRef
from agent.factcheck.loop_tools import EvidenceRow

def _draft(**kw):
    base = dict(
        central_question="q", action="verify", central_claim="c",
        headline_finding="h", justification="j",
        primary_sources=[DraftSource(url="https://fbi.gov/x", display_name="FBI"),
                         DraftSource(url="https://americanprogress.org/y", display_name="AP")],
        load_bearing_facts=["13,000 is a category error"],
        peripheral_facts=["US had ~16,935 homicides in 2024"],
        evidence_refs=[EvidenceRef(row=0, stance="refutes", on_point=True)],
        verdict_derivation="d", confidence="high", verdict_leaning="refuted",
    )
    base.update(kw)
    return DraftVerdict(**base)

def test_apply_scoped_drops_removes_peripheral_fact_and_source():
    rows = [EvidenceRow(idx=0, url="https://americanprogress.org/y", title="", snippet="",
                        body_markdown="b", published_at="2026-05-05", origin="fetch")]
    d = _draft()
    out = apply_scoped_drops(
        d,
        ["US had ~16,935 homicides in 2024", "https://americanprogress.org/y"],
        rows,
    )
    assert "US had ~16,935 homicides in 2024" not in out.peripheral_facts
    assert all(s.url != "https://americanprogress.org/y" for s in out.primary_sources)
    assert all(r.row != 0 for r in out.evidence_refs)   # ref to dropped url removed
    # central fact + verdict untouched
    assert out.load_bearing_facts == ["13,000 is a category error"]
    assert out.verdict_leaning == "refuted"

def test_to_report_carries_scoped_drops():
    out = VerifierOutput(passed=True, scoped_drops=["drop me"])
    rep = _to_report(out, False)
    assert rep.scoped_drops == ("drop me",)
    assert rep.passed is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_verifier.py -k "scoped_drops" -v`
Expected: FAIL — `VerifierOutput` has no `scoped_drops`; `apply_scoped_drops` not defined.

- [ ] **Step 3: Add `scoped_drops` to `VerifierOutput` and `VerifierReport`**

In `agent/factcheck/verifier.py`, add to `VerifierOutput` (after `injection_flags`):

```python
    scoped_drops: list[str] = Field(default_factory=list)
```

In `agent/factcheck/schema.py`, add to `VerifierReport` (after `revision_used`):

```python
    scoped_drops: tuple[str, ...] = Field(default_factory=tuple)
```

Update `_to_report` in `verifier.py` to pass it through:

```python
def _to_report(out: VerifierOutput, revision_used: bool) -> VerifierReport:
    return VerifierReport(
        passed=out.passed,
        temporal_leaks=tuple(out.temporal_leaks),
        derivation_gaps=tuple(out.derivation_gaps),
        lint_violations=tuple(out.lint_violations),
        injection_flags=tuple(out.injection_flags),
        fabrication_language_ok=out.fabrication_language_ok,
        required_revisions=out.required_revisions,
        downgrade=out.downgrade,
        revision_used=revision_used,
        scoped_drops=tuple(out.scoped_drops),
    )
```

- [ ] **Step 4: Add `apply_scoped_drops` in `verifier.py`**

Add after `scrub_temporal_leak`:

```python
def apply_scoped_drops(draft: DraftVerdict, drops: list[str],
                       rows: list[EvidenceRow]) -> DraftVerdict:
    """Remove verifier-flagged PERIPHERAL items from the reply-facing draft
    without touching the verdict. Each drop string is either an exact fact
    string (matched against load_bearing_facts / peripheral_facts) or a source
    URL (matched against primary_sources and the row backing an evidence_ref).
    Central verdict fields (verdict_leaning, headline_finding, counter_fact,
    verdict_derivation) are never modified here."""
    if not drops:
        return draft
    dropset = set(drops)
    by_idx = {r.idx: r for r in rows}
    lb = [f for f in draft.load_bearing_facts if f not in dropset]
    pf = [f for f in draft.peripheral_facts if f not in dropset]
    ps = [s for s in draft.primary_sources if s.url not in dropset]
    er = [e for e in draft.evidence_refs
          if by_idx.get(e.row) is None or by_idx[e.row].url not in dropset]
    return draft.model_copy(update={
        "load_bearing_facts": lb, "peripheral_facts": pf,
        "primary_sources": ps, "evidence_refs": er,
    })
```

- [ ] **Step 5: Run to verify Step-1 tests pass**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_verifier.py -k "scoped_drops" -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Write the failing test for the `run_verified_loop` scoped-fix flow**

Add to `tests/test_v07_verifier.py`:

```python
import agent.factcheck.verifier as vmod
from agent.factcheck.loop_tools import ToolRuntime

def test_run_verified_loop_scoped_fix_passes_without_scrub(monkeypatch):
    # Verifier returns a PERIPHERAL-only defect: passed=True + a scoped drop,
    # no central temporal leak. Expect: verdict kept, drop applied, no scrub.
    d = _draft()
    rt = ToolRuntime(cutoff=None)
    rt.rows = [EvidenceRow(idx=0, url="https://americanprogress.org/y", title="", snippet="",
                           body_markdown="b", published_at="2026-05-05", origin="fetch")]
    monkeypatch.setattr(vmod, "run_loop", lambda *a, **k: (d, rt, object(), []))
    monkeypatch.setattr(vmod, "verify_draft", lambda *a, **k: VerifierOutput(
        passed=True, scoped_drops=["US had ~16,935 homicides in 2024"]))
    draft, runtime, report, stats = vmod.run_verified_loop(
        "post", client=None, ctx=None, as_of=None, cutoff=None)
    assert report.passed is True
    assert draft.verdict_leaning == "refuted"                 # NOT scrubbed to insufficient
    assert "US had ~16,935 homicides in 2024" not in draft.peripheral_facts
    assert report.scoped_drops == ("US had ~16,935 homicides in 2024",)
```

- [ ] **Step 7: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_verifier.py::test_run_verified_loop_scoped_fix_passes_without_scrub -v`
Expected: FAIL — current `run_verified_loop` ignores `scoped_drops` (draft still has the peripheral fact) and does not apply them before the pass return.

- [ ] **Step 8: Rewrite `run_verified_loop` to apply scoped drops**

Replace the body of `run_verified_loop` (from the `out = verify_draft(...)` line onward, keeping the `run_loop` call and the `draft is None` guard above it unchanged):

```python
    out = verify_draft(draft, runtime.rows, post_text=post_text, as_of=as_of, cutoff=cutoff)
    # Peripheral defects are auto-applied and never block the verdict.
    draft = apply_scoped_drops(draft, out.scoped_drops, runtime.rows)
    if out.passed:
        return draft, runtime, _to_report(out, False), stats

    # Central defect path: one revision round, then re-verify.
    revision_used = False
    if out.required_revisions.strip():
        revision_used = True
        revised, _ = revise_in_loop(messages, out.required_revisions,
                                    client=client, runtime=runtime, model=model)
        if revised is None:
            logger.warning("run_verified_loop: revision produced no draft — retrying once")
            revised, _ = revise_in_loop(
                messages,
                out.required_revisions
                + "\n\nIMPORTANT: resubmit the COMPLETE corrected draft via the "
                  "finalize tool — every required field must be present.",
                client=client, runtime=runtime, model=model)
        if revised is not None:
            draft = revised
            out = verify_draft(draft, runtime.rows, post_text=post_text,
                               as_of=as_of, cutoff=cutoff)
            draft = apply_scoped_drops(draft, out.scoped_drops, runtime.rows)
            if out.passed:
                return draft, runtime, _to_report(out, True), stats

    # Still failing on a CENTRAL defect → downgrade, never loop.
    out.downgrade = True
    if out.temporal_leaks:
        # temporal_leaks now means a CENTRAL post-cutoff fact the revision
        # could not re-source — the one case that warrants neutralizing the
        # reply-facing payload (a peripheral post-cutoff corroborator would
        # have been a scoped_drop instead).
        logger.warning("run_verified_loop: unfixed CENTRAL temporal leak — scrubbing payload: %s",
                       "; ".join(out.temporal_leaks)[:300])
        return scrub_temporal_leak(draft), runtime, _to_report(out, revision_used), stats
    return apply_downgrade(draft), runtime, _to_report(out, revision_used), stats
```

- [ ] **Step 9: Run the full verifier test file**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_verifier.py -v`
Expected: PASS (all). Update any pre-existing verifier test that constructed `DraftVerdict` with `hypotheses`/`target_hypothesis` (swap to `central_question=`), and any that asserted the old scrub-on-any-temporal-leak behavior (the scrub now requires `out.temporal_leaks` to represent a central leak — existing tests that set `temporal_leaks` and expect a scrub still pass because the flow is unchanged for that field).

- [ ] **Step 10: Commit**

```bash
git add agent/factcheck/verifier.py agent/factcheck/schema.py tests/test_v07_verifier.py
git commit -m "feat(verifier): scoped fixes for peripheral defects; scrub only central leaks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 4: Verifier prompt — central/peripheral + reputable enforcement

**Files:**
- Modify: `agent/factcheck/prompts/verifier.md`
- Test: `tests/test_v07_prompt_store.py`

**Interfaces:**
- Consumes: the verifier is called with `draft` (now including `central_question`, `load_bearing_facts`, `peripheral_facts`) and `evidence_log` (rows include `published_date`; tiers are derivable from URLs). It must emit `VerifierOutput` including `scoped_drops`.
- Produces: prompt text instructing the classification. No code interface change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v07_prompt_store.py`:

```python
def test_verifier_prompt_covers_scoped_drops_and_tiers():
    from agent.factcheck.prompt_store import load_prompt
    text = load_prompt("verifier")
    assert "scoped_drops" in text
    assert "peripheral" in text.lower()
    assert "central" in text.lower()
    # reputable-source enforcement for central facts
    assert "reputable" in text.lower() or "fact-checker" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_prompt_store.py::test_verifier_prompt_covers_scoped_drops_and_tiers -v`
Expected: FAIL — `scoped_drops` not in the current prompt.

- [ ] **Step 3: Rewrite the verifier prompt**

Add the following section to `agent/factcheck/prompts/verifier.md` (place it before the output-schema description; keep the existing UNTRUSTED-page handling, temporal, and injection guidance). Verbatim text to insert:

```markdown
## Central vs peripheral — remediate, don't collapse

Classify every defect you find as CENTRAL or PERIPHERAL before you decide `passed`.

- The **central claim** is what the reader cares about — the thing `headline_finding`,
  `verdict_leaning`, and `counter_fact` assert. Its support is `load_bearing_facts`.
- **Peripheral** items are supporting numbers, side-facts, and corroborating sources
  (`peripheral_facts`, extra `primary_sources`) that colour the reply but do not carry the
  verdict.

Remediation rule:

- A **peripheral** defect — a supporting number that doesn't match its source, a
  corroborating source published AFTER the cutoff *when a pre-cutoff source already
  supports the same point*, or a low-tier citation for a peripheral fact — does NOT fail
  the draft. Put the exact fact string or the source URL into `scoped_drops`; the pipeline
  removes it and ships the verdict. Set `passed: true` if no central defect remains.
- A **central** defect fails the draft (`passed: false`) and goes in `required_revisions`:
  the central claim lacks pre-cutoff reputable support; the ONLY source for a central
  `load_bearing_fact` is post-cutoff (record this in `temporal_leaks` — it may trigger a
  payload scrub); a fabrication-language violation; or an injection.

`temporal_leaks` is now for CENTRAL post-cutoff facts only. A post-cutoff *corroborator*
of an otherwise pre-cutoff-supported point is a `scoped_drops` entry, NOT a temporal leak.

## Reputable-source enforcement (central facts)

Every `load_bearing_fact` must trace to a reputable tier — `fact-checker`,
`reputable-news`, or `primary-source` (infer the tier from the source domain and the
evidence log). If a central fact is backed ONLY by low-quality/unknown/aggregator sources,
require a better source or a hedge via `required_revisions`. Do NOT demand a correction
that the evidence doesn't warrant — the H0 default (the post may be accurate) still holds,
and an accurate post that survives scrutiny passes.
```

Update the output-schema portion of the prompt to list `scoped_drops` (array of strings: exact fact strings or source URLs to remove) alongside the existing fields.

- [ ] **Step 4: Run the prompt-store tests**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_prompt_store.py -v`
Expected: PASS (including the new test and the existing UNTRUSTED/temporal assertions).

- [ ] **Step 5: Commit**

```bash
git add agent/factcheck/prompts/verifier.md tests/test_v07_prompt_store.py
git commit -m "feat(verifier-prompt): central/peripheral classification + reputable enforcement

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 5: Playbook — evidence-first flow

**Files:**
- Modify: `agent/factcheck/prompts/loop_playbook.md`
- Test: `tests/test_v07_prompt_store.py`

**Interfaces:**
- Consumes: the loop's `finalize` tool schema is `DraftVerdict.model_json_schema()` (Task 2), so the playbook must describe `central_question`, `load_bearing_facts` (central), and `peripheral_facts`.
- Produces: prompt text only.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v07_prompt_store.py`:

```python
def test_playbook_is_evidence_first_not_hypothesis_first():
    from agent.factcheck.prompt_store import load_prompt
    text = load_prompt("loop_playbook")
    low = text.lower()
    # evidence-first flow present
    assert "broad" in low and "deepen" in low
    assert "central_question" in text
    assert "peripheral" in low
    # bias guards retained
    assert "h0" in low or "accurate and fairly framed" in low
    assert "endorsement cap" in low
    # temporal + untrusted retained (existing invariants)
    assert "UNTRUSTED" in text
    assert "Temporal contract" in text or "TEMPORAL" in text.upper()
    # hypothesis-first enumeration removed
    assert "list 2–4 ways" not in text and "list 2-4 ways" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_prompt_store.py::test_playbook_is_evidence_first_not_hypothesis_first -v`
Expected: FAIL — current playbook is hypothesis-first (`central_question` absent; "list 2–4 ways" present).

- [ ] **Step 3: Rewrite playbook §2–§3 and the finalize fields**

In `agent/factcheck/prompts/loop_playbook.md`, replace the current `## 2. Frame the check` and `## 3. Pick a target` sections with the following two sections (keep §1 Temporal contract, §5 Weigh evidence, §6 gate + endorsement cap, §7 Write the reply, §8 Finalize; renumber as needed):

```markdown
## 2. Orient — a broad, temporally-bounded sweep
Your job is to establish **what authoritative sources actually say about this claim** — not
to assume it misleads. Default reading is **H0: accurate and fairly framed — no correction
warranted**, and H0 holds until evidence positively displaces it; affirming a true post is
a correct outcome. Open with a broad first wave (4–8 searches) oriented at the *facts of
the claim*: the official series/record that answers it (EIA/BLS/FRED, CDC/WHO, FBI UCR,
court dockets, filings, official transcripts), the claim keywords + explicit month/year,
any quote verbatim in double quotes, a fact-checker sweep, and a media-provenance search
when relevant. Read what comes back before deciding anything.

## 3. Deepen — pick the threads that decide the verdict
From what the sweep returns, name the **1–3 load-bearing threads** that actually settle
the claim, and run targeted follow-up searches + `fetch_page` on those. This is where you
apply lenses to what you found — is a quote fabricated, a window cherry-picked, a
denominator missing, a category conflated, a cause misattributed? — as questions ABOUT the
evidence, not as a pre-committed list of ways the post is guilty. Prefer **reputable
sources** (`fetch_page` reports each source's `source_tier`): a central fact should rest on
a fact-checker, reputable-news, or primary-source tier. Fetch and READ pages before citing
any number, date, name, or quote.
```

Update the `## 8. Finalize` section (or wherever finalize fields are described) so it names the new fields:

```markdown
When you finalize, separate the facts by role:
- `central_question`: the one question your verdict answers.
- `load_bearing_facts`: the CENTRAL facts the verdict stands on — each must trace to a
  fetched, pre-cutoff, reputable source.
- `peripheral_facts`: supporting or colour details. If one is uncertain, post-cutoff, or
  only weakly sourced, it can be dropped without changing the verdict — put it here, not in
  load_bearing_facts.
Reference only evidence rows you actually retrieved.
```

Keep the adversarial gate + endorsement cap section (§6) verbatim.

- [ ] **Step 4: Run the prompt-store tests**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_prompt_store.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add agent/factcheck/prompts/loop_playbook.md tests/test_v07_prompt_store.py
git commit -m "feat(playbook): evidence-first flow (orient -> deepen), retire hypothesis enumeration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 6: Source-list coverage expansion

**Files:**
- Modify: `agent/factcheck/data/source_lists.json` (editorial supplement blocks)
- Test: `tests/test_v07_loop_tools.py` (extend the curated_tier tests)

**Interfaces:** data-only; validated through `curated_tier`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v07_loop_tools.py`:

```python
import pytest

@pytest.mark.parametrize("url,expected", [
    ("https://www.bbc.com/news/x", "reputable-news"),
    ("https://gasprices.aaa.com/", "primary-source"),
    ("https://www.factcheck.org/2024/x/", "fact-checker"),
])
def test_curated_tier_covers_common_reference_domains(url, expected):
    tier, _ = curated_tier(url)
    assert tier == expected
```

- [ ] **Step 2: Run to verify it fails (or passes) — establish the coverage gaps**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py -k covers_common_reference -v`
Expected: any parameter that returns `unknown` is a gap to fill in Step 3. (BBC/FactCheck are likely already covered via RSP/IFCN; AAA fuel may be a gap.)

- [ ] **Step 3: Add gap domains to the editorial supplement**

In `agent/factcheck/data/source_lists.json`, under the editorial block's `domains`, add the observed gap domains to the correct tier arrays. Add at minimum (only those returning `unknown` in Step 2):
- `primary-source`: `"gasprices.aaa.com"`, `"newsroom.aaa.com"`, `"fbi.gov"`, `"eia.gov"`, `"bls.gov"`, `"cdc.gov"`
- `low-quality`: `"grokipedia.com"`
Keep entries alphabetized within each tier if the file already is. Do not touch the IFCN/RSP blocks (those regenerate from feeds via `scripts/refresh_source_lists.py`).

- [ ] **Step 4: Run to verify the coverage tests pass**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/test_v07_loop_tools.py -k "curated_tier or covers_common_reference" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/factcheck/data/source_lists.json tests/test_v07_loop_tools.py
git commit -m "feat(sources): expand editorial supplement for common reference + primary domains

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

---

## Task 7: Re-validation against the acceptance bar

**Files:**
- Use: `.claude/debias_regression.py` (existing 3-post balance regression)
- Use: `/tmp/.../scratchpad/capture_runner.py` (existing 10-post capture runner) OR re-run via `study/scripts/batch_generate_replies.py --engine loop --study-mode`
- Create: `docs/v08-validation-2026-07-16.md` (results record)

**Interfaces:** no code; runs the pipeline end-to-end and records results.

- [ ] **Step 1: Full unit suite green**

Run: `/homes/gws/advaitmb/miniconda/bin/python -m pytest tests/ -q`
Expected: all pass. Fix any stragglers before proceeding.

- [ ] **Step 2: Balance regression (must hold)**

Run: `DERAD_FACTCHECK_ENGINE=loop /homes/gws/advaitmb/miniconda/bin/python .claude/debias_regression.py`
Expected: cherry-pick → context (not endorsed), fabrication → refuted, accurate → supported/context (not refuted). Record outcomes.

- [ ] **Step 3: Re-run the 10-post explorer set through v0.8**

Run the existing capture runner (engine=loop, study mode) on the same 10 post IDs used in `study_explorer_data.json`. Expected deltas vs v0.7:
- The "13,000 killed" post (`2043724976360968317`) ships **`verified_refuted`**, not `verified_nei`.
- No post that a Community Note could conclude comes back NEI unless its central claim is genuinely unsettleable from pre-cutoff evidence.
- 0 post-cutoff **central** citations; any dropped corroborators appear in `verifier_report.scoped_drops`.

- [ ] **Step 4: Held-out replay (generalization)**

Run the 15-post decontaminated held-out set (seed 42) through v0.8 and grade vs Community Notes with the symmetric rubric. **Acceptance:** accuracy vs Community Notes ≥ the v0.7 numbers recorded in `docs/v07-validation-2026-07-10.md`; NEI rate strictly lower on notes-conclusive posts; endorsement cap holds (0 endorsements of misleading posts).

- [ ] **Step 5: Record results + commit**

Write `docs/v08-validation-2026-07-16.md` with: the balance-regression outcomes, the 10-post outcome table (v0.7 → v0.8 deltas), the held-out accuracy vs v0.7, and a PASS/FAIL against each acceptance criterion.

```bash
git add docs/v08-validation-2026-07-16.md
git commit -m "docs: v0.8 evidence-first validation results

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016WTQLrxKD7rH4he4UWfQU7"
```

- [ ] **Step 6: Gate** — if any acceptance criterion fails, STOP and report; do not regenerate study stimuli with v0.8 until the bar is met.

---

## Notes for the executor

- **Ordering matters:** Task 2 changes `DraftVerdict`'s required fields, which breaks the `finalize` schema and several test constructions; do Tasks 1→2→3→4→5→6→7 in order. Task 1 is independent and safe to do first.
- **Coupling:** Task 3 (verifier code) defines the `scoped_drops` contract; Task 4 (verifier prompt) makes the LLM emit it. Task 3's tests use synthetic `VerifierOutput` objects (no LLM), so they validate the mechanism without waiting on Task 4.
- **Do not** flip the engine default, delete the staged pipeline, or regenerate the 108-post study set as part of this plan — those remain separate, sign-off-gated items.
