"""Audit-fail evidence neutralisation.

When the Stage-5 audit fails, the pipeline swaps findings/payload/justification
to an NEI shape. Reconcile-stamped evidence (with stance='supports'/'refutes')
must also be re-stamped to 'neutral' before Claim objects are constructed —
otherwise the frozen record carries supporting/refuting evidence under a
NotEnoughEvidence verdict, an internally inconsistent state that confuses any
forensic reader (e.g. the /info page).
"""
from __future__ import annotations

from agent.factcheck.pipeline import _neutralise_evidence_stances
from agent.factcheck.schema import Evidence


def test_neutralise_resets_supports_and_refutes_stances():
    evidence = [
        Evidence(
            question="q1",
            source_url="https://reuters.com/a",
            snippet="snippet a",
            stance="supports",
            body_markdown="body a",
        ),
        Evidence(
            question="q2",
            source_url="https://ap.org/b",
            snippet="snippet b",
            stance="refutes",
            body_markdown="body b",
        ),
        Evidence(
            question="q3",
            source_url="https://bbc.com/c",
            snippet="snippet c",
            stance="neutral",
            body_markdown="",
        ),
    ]

    out = _neutralise_evidence_stances(evidence)

    assert [e.stance for e in out] == ["neutral", "neutral", "neutral"]
    # Everything else preserved for traceability.
    assert [e.question for e in out] == ["q1", "q2", "q3"]
    assert [e.source_url for e in out] == [
        "https://reuters.com/a",
        "https://ap.org/b",
        "https://bbc.com/c",
    ]
    assert [e.snippet for e in out] == ["snippet a", "snippet b", "snippet c"]
    assert [e.body_markdown for e in out] == ["body a", "body b", ""]


def test_neutralise_empty_list_is_noop():
    assert _neutralise_evidence_stances([]) == []
