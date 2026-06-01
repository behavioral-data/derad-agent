"""Tests for `_iter_markdown_links`: balanced-paren URL extraction."""

from agent.factcheck.search import _iter_markdown_links


def _links(text):
    return [(title, url) for title, url, _, _ in _iter_markdown_links(text)]


def test_plain_link_with_trailing_slash():
    text = "See [Snopes](https://snopes.com/fact-check/x-101/) for more."
    assert _links(text) == [("Snopes", "https://snopes.com/fact-check/x-101/")]


def test_wikipedia_parenthesized_link():
    text = "Read [Mercury](https://en.wikipedia.org/wiki/Mercury_(element)) about it."
    assert _links(text) == [
        ("Mercury", "https://en.wikipedia.org/wiki/Mercury_(element)")
    ]


def test_two_links_one_parenthesized():
    text = (
        "First [a](https://example.com/a) and then "
        "[Foo](https://en.wikipedia.org/wiki/Foo_(disambiguation)) end."
    )
    assert _links(text) == [
        ("a", "https://example.com/a"),
        ("Foo", "https://en.wikipedia.org/wiki/Foo_(disambiguation)"),
    ]


def test_link_followed_by_sentence_punctuation():
    text = "Source: [x](https://x.com/a)…and then more."
    # The closing `)` of the markdown link ends the URL; the trailing
    # ellipsis is not consumed.
    assert _links(text) == [("x", "https://x.com/a")]


def test_no_false_positive_on_bare_url():
    text = "Visit https://example.com/page for details."
    assert _links(text) == []


def test_no_false_positive_on_non_markdown_paren_link():
    text = "Reference (https://example.com/page) inline."
    assert _links(text) == []
