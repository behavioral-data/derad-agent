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
