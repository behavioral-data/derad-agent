"""Tests for the stance-drift metric wiring in `agent.factcheck.reconcile`.

These guard the narrowed import handler: only the test-isolation case
(`agent.app.metrics` not present at all) should silently fall back to a
no-op; any other import failure in that module must bubble at module load
so production loses no telemetry. The stance-drift counter itself is
covered by exercising the count-mismatch path in `reconcile()`.
"""

from __future__ import annotations

import builtins
import importlib
import logging
import sys

import pytest

from agent.factcheck import reconcile as reconcile_module
from agent.factcheck.context import PipelineContext
from agent.factcheck.reconcile import ReconciliationOutput, reconcile
from agent.factcheck.schema import (
    ConsolidatedFindings,
    Lens1,
    PresentationPayload,
    UnaddressedProposition,
)


def _fake_output(stance_count: int) -> ReconciliationOutput:
    return ReconciliationOutput(
        lens_1=Lens1(narrative="n/a"),
        consolidated_findings=ConsolidatedFindings(
            unaddressed_propositions=(
                UnaddressedProposition(
                    proposition="claim",
                    reason="evidence retrieved but silent",
                    is_central=True,
                ),
            ),
        ),
        presentation_payload=PresentationPayload(
            headline_finding="n/a",
            counter_fact=None,
            primary_sources_to_cite=(),
            load_bearing_evidence_snippet="",
        ),
        tone_neutral_justification="n/a",
        evidence_stances=["neutral"] * stance_count,
    )


class TestStanceDriftCounter:
    def test_counter_is_bound_at_module_load(self):
        """In a normal test run the metrics module imports cleanly, so
        the module-level counter handle is wired up (not None)."""
        assert reconcile_module._reconcile_stance_drift is not None

    def test_stance_drift_increments_counter(self, monkeypatch):
        """When Claude returns more stances than evidence rows, the
        reconcile stage pads/truncates AND increments the drift counter
        with the size delta."""
        calls: list[tuple[int, dict]] = []

        class _Spy:
            def add(self, n, attrs):
                calls.append((n, dict(attrs)))

        monkeypatch.setattr(reconcile_module, "_reconcile_stance_drift", _Spy())
        monkeypatch.setattr(
            reconcile_module,
            "call_claude_json",
            lambda **_: _fake_output(stance_count=3),
        )

        out = reconcile(
            central_claim_text="some claim",
            evidence=[],  # 0 evidence rows, but Claude emits 3 stances
            source_quality_table=[],
            ctx=PipelineContext(),
        )

        assert calls == [(1, {"delta": "3"})]
        assert len(out.evidence_stances) == 0  # truncated to match evidence

    def test_stance_drift_noops_when_counter_unbound(self, monkeypatch):
        """When the metrics module wasn't importable (e.g. unit test
        without the Flask app on the path), the counter handle is None
        and the drift path quietly falls through — no AttributeError."""
        monkeypatch.setattr(reconcile_module, "_reconcile_stance_drift", None)
        monkeypatch.setattr(
            reconcile_module,
            "call_claude_json",
            lambda **_: _fake_output(stance_count=2),
        )

        # Must not raise.
        out = reconcile(
            central_claim_text="some claim",
            evidence=[],
            source_quality_table=[],
            ctx=PipelineContext(),
        )
        assert len(out.evidence_stances) == 0


class TestImportHandler:
    """Verify the narrowed except clause at module top level.

    The handler must swallow `ModuleNotFoundError` (legitimate
    test-isolation case) and let any other exception type bubble — so a
    broken submodule import inside `agent.app.metrics` is loud rather
    than silent telemetry loss.
    """

    def _reimport_with_metrics_blocked(self, blocker):
        """Re-import reconcile with `agent.app.metrics` import patched.

        `blocker` receives the original __import__ and the import name;
        it can raise to simulate an import-time failure or delegate.
        """
        # Drop cached modules so the top-level import block re-runs.
        for name in ("agent.factcheck.reconcile", "agent.app.metrics"):
            sys.modules.pop(name, None)

        original_import = builtins.__import__

        def _patched(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "agent.app" and fromlist and "metrics" in fromlist:
                return blocker(original_import, name, fromlist)
            if name == "agent.app.metrics":
                return blocker(original_import, name, fromlist)
            return original_import(name, globals, locals, fromlist, level)

        builtins.__import__ = _patched
        try:
            return importlib.import_module("agent.factcheck.reconcile")
        finally:
            builtins.__import__ = original_import
            # Restore the real module for downstream tests.
            sys.modules.pop("agent.factcheck.reconcile", None)
            importlib.import_module("agent.factcheck.reconcile")

    def test_module_not_found_is_swallowed(self):
        """Missing `agent.app.metrics` ⇒ handle becomes None, no crash."""

        def _raise_mnfe(*_args, **_kwargs):
            raise ModuleNotFoundError("No module named 'agent.app.metrics'")

        mod = self._reimport_with_metrics_blocked(_raise_mnfe)
        assert mod._reconcile_stance_drift is None

    def test_other_import_errors_bubble(self):
        """A broken submodule import inside `agent.app.metrics` (not a
        missing-module case) must NOT be silently swallowed — that's the
        production telemetry-loss bug we're guarding against."""

        def _raise_import_error(*_args, **_kwargs):
            # Simulate e.g. a broken OpenTelemetry exporter import inside
            # agent.app.metrics. ImportError but NOT ModuleNotFoundError.
            raise ImportError("broken OTel exporter import")

        with pytest.raises(ImportError, match="broken OTel exporter"):
            self._reimport_with_metrics_blocked(_raise_import_error)
