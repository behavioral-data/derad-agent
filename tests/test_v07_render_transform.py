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
_BAD_SAT = "Gas is a luxury good now, congrats everyone."


def test_all_tones_pass_when_facts_survive():
    with mock.patch("agent.factcheck.render.render", return_value=_NEUTRAL), \
         mock.patch("agent.factcheck.render._transform_register",
                    side_effect=[_GOOD_SAT, _NEUTRAL + " I understand the concern."]):
        out = render_all_tones(_VIEW)
    assert set(out) == {"neutral", "satirical", "agreeable"}
    assert out["satirical"] == _GOOD_SAT


def test_lint_failing_variant_retries_then_falls_back_to_neutral():
    with mock.patch("agent.factcheck.render.render", return_value=_NEUTRAL), \
         mock.patch("agent.factcheck.render._transform_register",
                    side_effect=[_BAD_SAT, _BAD_SAT, _BAD_SAT,       # satirical: fails all retries
                                 _NEUTRAL + " Understandable worry."]):
        out = render_all_tones(_VIEW, max_lint_retries=2)
    assert out["satirical"] == out["neutral"]        # fallback, never ship lint-failing text


def test_neutral_lint_retry_uses_clean_render():
    # First neutral render carries a foreign numeral (27 — not in the frozen
    # payload/justification); the retry is clean. The clean retry must be the
    # shipped neutral text, and the register transforms must derive from it.
    dirty = "Prices rose 27 cents this week."     # 27 not in payload → substance lint fails
    clean = _NEUTRAL
    with mock.patch("agent.factcheck.render.render", side_effect=[dirty, clean]) as rnd, \
         mock.patch("agent.factcheck.render._transform_register",
                    side_effect=lambda neutral, tone, view, feedback="": f"{neutral} [{tone}]"):
        out = render_all_tones(_VIEW)
    assert rnd.call_count == 2                     # re-rendered once after the dirty attempt
    assert out["neutral"] == clean
    # transforms derive from the CLEAN neutral text, not the dirty first attempt
    assert out["satirical"] == f"{clean} [satirical]"
    assert out["agreeable"] == f"{clean} [agreeable]"
