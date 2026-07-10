from agent.factcheck.render_lint import extract_numerals, lint_cross_tone, lint_substance
from agent.factcheck.schema import PresentationPayload

_PAYLOAD = PresentationPayload(
    headline_finding="Prices up 44% since January, from $2.81 to $4.02.",
    load_bearing_facts=("44%", "$2.81", "$4.02"),
)


def test_extract_numerals_normalizes():
    assert extract_numerals("Up 44% from $2.81; 2,000 more") == {"44%", "$2.81", "2000"}


def test_lint_substance_passes_payload_numbers():
    assert lint_substance("Prices rose 44% from $2.81.", _PAYLOAD, "") == []


def test_lint_substance_flags_foreign_number():
    out = lint_substance("Prices rose 27 cents this week.", _PAYLOAD, "")
    assert any("27" in v for v in out)


def test_lint_substance_flags_pipeline_leak():
    out = lint_substance("PitchBook failed to load during fact-checking.", _PAYLOAD, "")
    assert any("failed to load" in v for v in out)
    # Natural prose containing substrings of the old, overbroad markers
    # ("pipeline", "cutoff") must NOT be flagged — false positives here trip
    # neutral-fallback, a research-validity risk.
    assert lint_substance("The oil pipeline disruption pushed prices up 44%.", _PAYLOAD, "") == []
    assert lint_substance("The income cutoff sits at $2.81.", _PAYLOAD, "") == []


def test_lint_substance_accepts_reframed_decorations():
    # "44 percent" / bare "2.81" carry the same numbers as the payload's
    # "44%" / "$2.81" — decoration-stripped comparison must accept them.
    assert lint_substance("Prices rose 44 percent from 2.81.", _PAYLOAD, "") == []


def test_lint_cross_tone_numeral_subset_branch():
    # Fact "$2.81" is not a literal substring of the text, but its stripped
    # numeral appears — the numeral-subset branch of _fact_in must match.
    assert lint_cross_tone({"neutral": "went from 2.81 to 4.02"}, ("$2.81",)) == []


def test_lint_cross_tone_flags_missing_fact():
    texts = {"neutral": "Up 44% from $2.81 to $4.02.",
             "satirical": "Gas is basically a luxury good now.",
             "agreeable": "Up 44% from $2.81 to $4.02, I get the concern."}
    out = lint_cross_tone(texts, _PAYLOAD.load_bearing_facts)
    assert any(v.startswith("satirical") for v in out)
    assert not any(v.startswith("neutral") for v in out)
