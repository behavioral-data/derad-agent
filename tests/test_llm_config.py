"""Tests for agent.llm.config._parse_bool_env.

Regression coverage for a bug where only the literal "true" was treated as
truthy, so DERAD_DRY_RUN=1 (or "yes"/"on") silently coerced to False and the
bot ran LIVE instead of in dry-run mode.
"""

from __future__ import annotations

import pytest

from agent.llm.config import _parse_bool_env

VAR = "DERAD_TEST_BOOL_FLAG"


@pytest.mark.parametrize("raw", ["1", "yes", "on", "TRUE", "true", "True", "Y", "t"])
def test_truthy_values_parse_true(monkeypatch, raw):
    monkeypatch.setenv(VAR, raw)
    assert _parse_bool_env(VAR) is True


@pytest.mark.parametrize("raw", ["0", "no", "off", "false", "", "garbage"])
def test_falsy_values_parse_false(monkeypatch, raw):
    monkeypatch.setenv(VAR, raw)
    assert _parse_bool_env(VAR) is False


def test_default_honored_when_unset(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    assert _parse_bool_env(VAR) is False
    assert _parse_bool_env(VAR, default=True) is True
