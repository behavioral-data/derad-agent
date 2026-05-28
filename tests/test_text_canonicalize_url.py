"""Unit tests for agent.shared.text.canonicalize_url."""

from agent.shared.text import canonicalize_url


def test_strips_trailing_period():
    assert (
        canonicalize_url("https://www.snopes.com/fact-check/x-101/.")
        == "https://www.snopes.com/fact-check/x-101/"
    )


def test_strips_trailing_comma():
    assert (
        canonicalize_url("https://example.com/foo,")
        == "https://example.com/foo"
    )


def test_strips_combined_trailing_punctuation():
    assert (
        canonicalize_url("https://example.com/foo?!")
        == "https://example.com/foo"
    )


def test_strips_unbalanced_trailing_paren():
    assert (
        canonicalize_url("https://example.com/foo)")
        == "https://example.com/foo"
    )


def test_keeps_balanced_paren_in_wikipedia_url():
    assert (
        canonicalize_url("https://en.wikipedia.org/wiki/Mercury_(element)")
        == "https://en.wikipedia.org/wiki/Mercury_(element)"
    )


def test_strips_paren_and_following_punctuation():
    assert (
        canonicalize_url("https://example.com/foo).")
        == "https://example.com/foo"
    )


def test_strips_fragment():
    assert (
        canonicalize_url("https://example.com/foo#section")
        == "https://example.com/foo"
    )


def test_strips_default_port_http():
    assert (
        canonicalize_url("http://example.com:80/foo")
        == "http://example.com/foo"
    )


def test_strips_default_port_https():
    assert (
        canonicalize_url("https://example.com:443/foo")
        == "https://example.com/foo"
    )


def test_keeps_non_default_port():
    assert (
        canonicalize_url("https://example.com:8080/foo")
        == "https://example.com:8080/foo"
    )


def test_lowercases_scheme_and_host():
    assert (
        canonicalize_url("HTTPS://Example.COM/Foo/Bar")
        == "https://example.com/Foo/Bar"
    )


def test_preserves_query_case():
    assert (
        canonicalize_url("https://example.com/foo?Q=AbC")
        == "https://example.com/foo?Q=AbC"
    )


def test_returns_original_on_malformed_input():
    assert canonicalize_url("not a url") == "not a url"


def test_empty_string_passthrough():
    assert canonicalize_url("") == ""
