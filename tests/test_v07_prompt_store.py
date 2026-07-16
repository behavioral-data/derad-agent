from agent.factcheck import prompt_store


def test_load_prompt_returns_playbook_text():
    text = prompt_store.load_prompt("loop_playbook")
    assert "Temporal contract" in text or "TEMPORAL" in text.upper()
    assert "UNTRUSTED" in text  # injection framing present


def test_prompt_version_is_12_hex_and_stable():
    v1 = prompt_store.prompt_version()
    v2 = prompt_store.prompt_version()
    assert v1 == v2
    assert len(v1) == 12
    int(v1, 16)  # parses as hex


def test_unknown_prompt_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        prompt_store.load_prompt("nope")


def test_verifier_prompt_covers_scoped_drops_and_tiers():
    from agent.factcheck.prompt_store import load_prompt
    text = load_prompt("verifier")
    assert "scoped_drops" in text
    assert "peripheral" in text.lower()
    assert "central" in text.lower()
    # reputable-source enforcement for central facts
    assert "reputable" in text.lower() or "fact-checker" in text.lower()


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
