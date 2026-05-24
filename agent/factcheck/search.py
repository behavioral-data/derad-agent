"""Search backend interface + thin-slice stub.

The real backend will be a thin Foundry-Agent-Service wrapper exposing only
the `bing_grounding` tool. For the thin slice we use a canned provider that
returns hardcoded hits for one example claim — enough to exercise the
pipeline shape end-to-end without provisioning Foundry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]: ...


class StubSearchBackend:
    """Hardcoded hits for one example. Drives the thin slice without external calls."""

    name = "stub:canned-rosa-camfield"

    _CANNED: dict[str, list[SearchHit]] = {
        "rosa camfield": [
            SearchHit(
                url="https://www.snopes.com/fact-check/rosa-camfield-101/",
                title="Did a 101-Year-Old Woman Give Birth to Her 17th Child?",
                snippet=(
                    "A photograph showing an elderly woman with a newborn is real, but the "
                    "caption is false. The image shows 101-year-old Rosa Camfield holding her "
                    "two-week-old great-granddaughter Kaylee in March 2015. The claim that the "
                    "woman gave birth to her 17th child at age 101 originated from World News "
                    "Daily Report, a self-described satirical and fictional news site."
                ),
            ),
            SearchHit(
                url="https://www.thequint.com/news/webqoof/woman-with-baby-not-mother-of-17-fake-news",
                title="Photo of 'Mother of 17 at 101' Is Miscaptioned",
                snippet=(
                    "The Quint traced the image to a 2015 Facebook post by the Camfield family. "
                    "Rosa Camfield, then 101, is shown meeting her great-granddaughter Kaylee. "
                    "The 'mother of 17' story is fabricated."
                ),
            ),
            SearchHit(
                url="https://africacheck.org/fact-checks/meta-programme-fact-checks/no-photo-doesnt-show-101-year-old-woman",
                title="No, photo doesn't show 101-year-old woman who gave birth to her 17th child",
                snippet=(
                    "Africa Check confirmed the photo is authentic but miscaptioned. The woman "
                    "is Rosa Camfield, and the baby is her great-granddaughter."
                ),
            ),
            SearchHit(
                url="https://worldnewsdailyreport.com/woman-101-gives-birth-to-her-17th-child/",
                title="Woman, 101, gives birth to her 17th child",
                snippet=(
                    "[SATIRE] The original source of the false claim. World News Daily Report "
                    "describes itself as satirical and entirely fictional."
                ),
            ),
        ],
    }

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        key = next((k for k in self._CANNED if k in query.lower()), None)
        if key is None:
            return []
        return self._CANNED[key][:top_k]
