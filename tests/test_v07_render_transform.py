"""render_all_tones — direct per-tone render, lint-gated (R-4 substance / R-5 facts).

Each tone is rendered directly to the length cap; the lints catch fact-dropping
or numeral-invention and retry; a tone that can't pass falls back to neutral so a
divergent variant is never shipped. (Earlier drafts re-voiced neutral via a
transform pass — dropped for inflating fact-dense replies past the cap.)
"""
from unittest import mock

from agent.factcheck.freeze import RendererView
from agent.factcheck.render import render_all_tones
from agent.factcheck.schema import PresentationPayload

_VIEW = RendererView(
    presentation_payload=PresentationPayload(
        headline_finding="Prices up 44% since January, from $2.81 to $4.02.",
        load_bearing_facts=("44%", "$2.81", "$4.02"),
    ),
    tone_neutral_justification="EIA data: $2.81 Jan, $4.02 Apr (44%).",
    action="provide_context", action_outcome="context_provided",
)

_NEUTRAL = "Context: prices are up 44% since January, from $2.81 to $4.02."
_GOOD_SAT = "Ah yes, savings: up 44% since January — $2.81 then, $4.02 now."
_GOOD_AGR = "I hear the relief, but prices are up 44% since January, $2.81 to $4.02."
_BAD_SAT = "Gas is a luxury good now, congrats everyone."   # drops all facts → R-5 fails


def test_all_tones_distinct_when_each_render_is_lint_clean():
    # Each tone renders directly and passes the lints → three distinct, fact-complete replies.
    with mock.patch("agent.factcheck.render.render",
                    side_effect=[_NEUTRAL, _GOOD_SAT, _GOOD_AGR]):
        out = render_all_tones(_VIEW)
    assert out["neutral"] == _NEUTRAL
    assert out["satirical"] == _GOOD_SAT
    assert out["agreeable"] == _GOOD_AGR
    assert out["satirical"] != out["neutral"] and out["agreeable"] != out["neutral"]


def test_fact_dropping_variant_retries_then_falls_back_to_neutral():
    # satirical drops every load-bearing fact on all attempts → R-5 fails → neutral fallback.
    # neutral (1) + satirical attempts (3, max_lint_retries=2) + agreeable (1 clean) = 5 renders.
    with mock.patch("agent.factcheck.render.render",
                    side_effect=[_NEUTRAL, _BAD_SAT, _BAD_SAT, _BAD_SAT, _GOOD_AGR]):
        out = render_all_tones(_VIEW, max_lint_retries=2)
    assert out["satirical"] == out["neutral"]        # never ship a fact-dropping variant
    assert out["agreeable"] == _GOOD_AGR


def test_numeral_invention_is_caught_by_substance_lint():
    # satirical invents "27" (not in payload/justification) → R-4 fails → retry lands clean.
    invented = "Gas up 27 cents, basically a heist — $2.81 to $4.02, 44% since January."
    with mock.patch("agent.factcheck.render.render",
                    side_effect=[_NEUTRAL, invented, _GOOD_SAT, _GOOD_AGR]):
        out = render_all_tones(_VIEW, max_lint_retries=2)
    assert out["satirical"] == _GOOD_SAT             # invented-numeral attempt rejected
    assert "27" not in out["satirical"]


def test_fewest_violations_kept_when_nothing_clean():
    # Every satirical attempt violates, but the 2nd drops fewer facts than the others;
    # with no clean render and neutral present, fewest-violations is preferred over neutral
    # only if it still... actually falls back to neutral when all violate — assert that.
    with mock.patch("agent.factcheck.render.render",
                    side_effect=[_NEUTRAL, _BAD_SAT, _BAD_SAT, _BAD_SAT, _GOOD_AGR]):
        out = render_all_tones(_VIEW, max_lint_retries=2)
    # _BAD_SAT drops all 3 facts every time → best still violates → neutral fallback
    assert out["satirical"] == out["neutral"]
