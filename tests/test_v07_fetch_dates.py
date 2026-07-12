from unittest import mock

from agent.factcheck.search import FetchedPage, _fetch_clean_page

_HTML = b"""<html><head><title>Gas prices fall</title>
<meta property="article:published_time" content="2026-04-21T09:00:00Z"></head>
<body><article><p>National average fell for the eighth day.</p></article></body></html>"""


class _FakeResp:
    status_code = 200
    url = "https://news.test/gas"
    encoding = "utf-8"
    def iter_content(self, chunk_size):  # noqa: ARG002
        yield _HTML
    def close(self):
        pass


def test_fetch_extracts_published_date():
    with mock.patch("requests.get", return_value=_FakeResp()):
        page = _fetch_clean_page("https://news.test/gas")
    assert isinstance(page, FetchedPage)
    assert page.status == 200
    assert page.published_date == "2026-04-21"
    assert "eighth day" in page.body_markdown


def test_fetch_failure_returns_none_status():
    with mock.patch("requests.get", side_effect=OSError("boom")):
        page = _fetch_clean_page("https://dead.test/x")
    assert page.status is None and page.published_date is None
