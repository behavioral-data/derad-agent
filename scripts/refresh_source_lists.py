#!/usr/bin/env python3
"""Refresh the source-reliability lists from authoritative public sources.

Writes ``agent/factcheck/data/source_lists.json``, the artifact loaded by
``agent.factcheck.sources``. Three provenance-tagged blocks:

  1. ifcn          — IFCN verified signatories (the network publishes the
                     canonical machine-readable list itself).
                     https://github.com/IFCN/verified-signatories
  2. wikipedia_rsp — Wikipedia:Reliable sources/Perennial sources. Each entry
                     carries a status code ({{RSPSTATUS|gr}}) and its domains
                     ({{RSPUSES|a.com|b.com}}), so the table is parseable.
                     Pinned to the per-subpage revision IDs fetched.
                     https://en.wikipedia.org/wiki/Wikipedia:RSP
  3. editorial     — hand-curated supplement for tiers with no clean public
                     feed (primary-source, satirical) plus gap-fillers. These
                     are OUR editorial judgement, recorded as such; tier_source
                     is "editorial-curated" so the paper can distinguish them
                     from externally-backed classifications.

Precedence at load time (sources.py): ifcn > wikipedia_rsp > editorial. The
editorial block only fills domains the external lists don't already cover.

Run:  python -m scripts.refresh_source_lists
      python -m scripts.refresh_source_lists --check   # fail if stale, no write

Requires network access to raw.githubusercontent.com and en.wikipedia.org.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "agent" / "factcheck" / "data" / "source_lists.json"
)

_UA = "derad-agent-research/1.0 (https://github.com/; source-list refresh)"

IFCN_RAW_URL = "https://raw.githubusercontent.com/IFCN/verified-signatories/main/list"
IFCN_COMMITS_API = (
    "https://api.github.com/repos/IFCN/verified-signatories/commits"
    "?path=list&per_page=1"
)
IFCN_PAGE_URL = "https://github.com/IFCN/verified-signatories"

RSP_PAGE_URL = "https://en.wikipedia.org/wiki/Wikipedia:RSP"
_RSP_MAIN = "Wikipedia:Reliable_sources/Perennial_sources"
_RSP_API = (
    "https://en.wikipedia.org/w/api.php?action=parse"
    "&page={page}&prop=wikitext|revid&format=json"
)

# RSP status code -> our SourceTier. nc (no consensus) and m (marginal) are
# deliberately NOT mapped: we leave genuinely-contested sources unclassified
# and let the model-prior fallback handle them rather than assert a tier.
_RSP_STATUS_TO_TIER = {
    "gr": "reputable-news",   # generally reliable
    "gu": "low-quality",      # generally unreliable
    "d":  "low-quality",      # deprecated
    "b":  "low-quality",      # blacklisted
    "bl": "low-quality",      # blacklisted (alt code)
}
_RSP_STATUS_SKIP = {"nc", "m", "mr"}  # no consensus / marginal — left unknown

# A bare registrable domain: lowercased host, no scheme/path/space/params.
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+$")


# ── Editorial supplement (source of truth) ───────────────────────────────────
# Tiers with no clean public feed, kept honest as "editorial-curated". Domains
# already covered by IFCN / RSP are ignored at load time — these only fill gaps.

_EDITORIAL: dict[str, list[str]] = {
    # Government, IGO, academic, peer-reviewed journals, institutions. RSP does
    # not model "primary source", so this stays editorial.
    "primary-source": [
        # US government
        "whitehouse.gov", "state.gov", "supremecourt.gov", "uscourts.gov",
        "archives.gov", "loc.gov", "congress.gov", "house.gov", "senate.gov",
        "cdc.gov", "fda.gov", "nih.gov", "noaa.gov", "epa.gov",
        "nasa.gov", "census.gov", "treasury.gov", "doe.gov",
        "defense.gov", "dod.gov", "justice.gov", "fbi.gov",
        "energy.gov", "ed.gov", "labor.gov", "doi.gov", "ftc.gov",
        "sec.gov", "irs.gov", "uscis.gov", "dhs.gov",
        # Other governments
        "gov.uk", "europa.eu", "ec.europa.eu", "europarl.europa.eu",
        "consilium.europa.eu", "echr.coe.int",
        "canada.ca", "gc.ca", "gov.au", "gov.nz",
        "india.gov.in", "rbi.org.in",
        # IGOs
        "who.int", "un.org", "imf.org", "worldbank.org", "oecd.org",
        "wto.org", "unesco.org", "unicef.org", "ilo.org", "wmo.int",
        "icrc.org", "iea.org", "iaea.org", "nato.int",
        # Academic / journals
        "nature.com", "science.org", "thelancet.com", "nejm.org",
        "jamanetwork.com", "bmj.com", "cell.com", "plos.org",
        "pnas.org", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
        "arxiv.org", "biorxiv.org", "medrxiv.org",
        "smithsonianmag.com",
        # nationalgeographic.com / scientificamerican.com intentionally NOT
        # here: they are magazines RSP rates "generally reliable", so we let
        # them resolve to reputable-news via RSP rather than override to
        # primary-source. jamanetwork.com (a journal) stays — it is primary.
    ],
    # RSP tracks some satire as "satire" in prose but not as a queryable status,
    # so we keep an explicit list.
    "satirical": [
        "theonion.com", "babylonbee.com", "worldnewsdailyreport.com",
        "clickhole.com", "thehardtimes.net", "reductress.com",
        "thedailymash.co.uk", "thebeaverton.com", "thepoke.co.uk",
        "the-postillon.com", "newsthump.com", "thespoof.com",
        "empirenews.net", "now8news.com", "huzlers.com",
        "newsbiscuit.com", "private-eye.co.uk",
        "rocketnewsfeed.com",
    ],
    # Nationally-recognized fact-checkers. The IFCN block (currently-verified
    # signatories) takes precedence, so any domain here that IS currently
    # verified keeps tier_source "ifcn"; only those whose IFCN verification has
    # lapsed/expired (verification is annual) fall back to this editorial entry.
    # As an org re-verifies it auto-flips back to "ifcn" with no code change.
    "fact-checker": [
        "snopes.com", "factcheck.org", "politifact.com", "leadstories.com",
        "checkyourfact.com", "fullfact.org", "factcheckni.org",
        "logicallyfacts.com", "factcheck.afp.com", "factual.afp.com",
        "factuel.afp.com", "thequint.com", "vishvasnews.com", "boomlive.in",
        "altnews.in", "factly.in", "newschecker.in", "factcrescendo.com",
        "fact-crescendo.com", "africacheck.org", "pesacheck.org", "dubawa.org",
        "namibiafactcheck.org.na", "verafiles.org", "rappler.com", "tjekdet.dk",
        "faktisk.no", "correctiv.org", "dpa-factchecking.com", "demagog.org.pl",
        "factcheck.kz", "stopfake.org", "maldita.es", "newtral.es", "efe.com",
        "facta.news", "pagellapolitica.it", "lavoce.info", "20minutes.fr",
        "checkdesk.org", "checkpoint.bg",
    ],
    # Reputable news with clear editorial standards. RSP only lists outlets that
    # have been *discussed* there (often because contested), so it is not a
    # comprehensive whitelist; this fills in uncontested mainstream / major
    # international outlets RSP never litigated. RSP takes precedence where it
    # does have an entry, so anything here that RSP rates stays "wikipedia-rsp".
    "reputable-news": [
        # United States
        "apnews.com", "reuters.com", "nytimes.com", "washingtonpost.com",
        "wsj.com", "usatoday.com", "latimes.com", "chicagotribune.com",
        "bostonglobe.com", "miamiherald.com", "houstonchronicle.com",
        "tampabay.com", "propublica.org", "themarshallproject.org",
        "npr.org", "abcnews.go.com", "cbsnews.com", "nbcnews.com",
        "cnn.com", "msnbc.com", "foxnews.com", "pbs.org",
        "bloomberg.com", "axios.com", "politico.com", "thehill.com",
        "time.com", "newsweek.com", "theatlantic.com", "newyorker.com",
        "vanityfair.com", "rollingstone.com", "wired.com", "vox.com",
        # United Kingdom
        "bbc.com", "bbc.co.uk", "theguardian.com", "telegraph.co.uk",
        "ft.com", "thetimes.co.uk", "independent.co.uk",
        "economist.com", "skynews.com", "channel4.com",
        # Europe
        "dw.com", "france24.com", "rfi.fr", "lemonde.fr", "liberation.fr",
        "spiegel.de", "zeit.de", "sueddeutsche.de", "tagesschau.de",
        "elpais.com", "elmundo.es", "corriere.it", "lastampa.it",
        "repubblica.it",
        # Australia / NZ
        "smh.com.au", "theage.com.au", "abc.net.au", "theguardian.com.au",
        "rnz.co.nz", "stuff.co.nz",
        # Canada
        "globeandmail.com", "cbc.ca", "nationalpost.com", "thestar.com",
        # India
        "thehindu.com", "indianexpress.com", "hindustantimes.com",
        "timesofindia.indiatimes.com", "ndtv.com", "scroll.in", "thewire.in",
        # Middle East
        "haaretz.com", "timesofisrael.com", "aljazeera.com",
        "thenationalnews.com",
        # East Asia
        "japantimes.co.jp", "asahi.com", "nhk.or.jp", "scmp.com",
        "straitstimes.com",
    ],
}


# ── Non-evidence domains (excluded from the evidence table entirely) ──────────
# These are neither reliable nor unreliable *sources* — they are not sources of
# factual claims at all (stock-photo marketplaces, shopping sites, UGC content
# farms). Reverse-image search in particular surfaces these constantly. They are
# dropped before classification so they never enter source_quality_table or
# reach reconcile. Editorial by nature — there is no public feed for "not a
# source". Subdomain → parent matching applies (so aws.amazon.com → amazon.com).
# NOT excluded (deliberately): major social/video platforms (youtube, x,
# facebook, reddit) — they can carry genuine primary evidence.
_NON_EVIDENCE: dict[str, list[str]] = {
    # Stock / image marketplaces
    "stock-image": [
        "gettyimages.com", "gettyimages.co.uk", "gettyimages.ca",
        "istockphoto.com", "istock.com",
        "shutterstock.com", "alamy.com", "dreamstime.com",
        "depositphotos.com", "123rf.com", "stock.adobe.com",
        "pixabay.com", "unsplash.com", "pexels.com", "freepik.com",
        "vecteezy.com", "bigstockphoto.com", "canstockphoto.com",
        "agefotostock.com", "picfair.com", "shutterstock.ai",
    ],
    # Shopping / e-commerce
    "shopping": [
        "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
        "amazon.es", "amazon.it", "amazon.ca", "amazon.in",
        "amazon.co.jp", "amazon.com.br", "amazon.com.mx",
        "ebay.com", "ebay.co.uk", "etsy.com", "aliexpress.com",
        "alibaba.com", "walmart.com", "target.com", "bestbuy.com",
        "temu.com", "wish.com", "shein.com", "flipkart.com",
        "rakuten.com", "wayfair.com", "overstock.com",
    ],
    # Pinboards / UGC content farms (no editorial standards)
    "ugc-farm": [
        "pinterest.com", "pinterest.co.uk", "pinterest.ca",
        "pinterest.fr", "pinterest.de", "pinterest.es", "pinterest.com.au",
        "quora.com", "answers.com", "ehow.com", "wikihow.com",
        "ask.com", "reference.com", "hubpages.com",
    ],
}


def _fetch(url: str, *, accept: str | None = None, _retries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    for attempt in range(_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < _retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  429 from {url} — backing off {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def _is_domain(token: str) -> bool:
    return bool(_DOMAIN_RE.match(token))


def _fetch_ifcn() -> dict:
    raw = _fetch(IFCN_RAW_URL).decode("utf-8")
    domains = sorted({
        ln.strip().lower()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#") and _is_domain(ln.strip().lower())
    })
    # Pin the commit that last touched the list, for provenance.
    commit = None
    try:
        commits = json.loads(_fetch(IFCN_COMMITS_API, accept="application/vnd.github+json"))
        if commits:
            commit = commits[0].get("sha")
    except Exception as e:  # provenance is best-effort; data still valid
        print(f"  warn: could not fetch IFCN commit sha ({e})", file=sys.stderr)
    return {
        "tier": "fact-checker",
        "tier_source": "ifcn",
        "rationale": "IFCN verified signatory.",
        "provenance": {
            "name": "IFCN verified signatories",
            "url": IFCN_RAW_URL,
            "repo": IFCN_PAGE_URL,
            "commit": commit,
        },
        "domains": domains,
    }


def _parse_rsp_rows(wikitext: str) -> list[tuple[str, list[str]]]:
    """Return (status_code, [domains]) per entry. Pairs each RSPSTATUS with the
    next RSPUSES block in document order (entries are one row each)."""
    tokens = re.findall(
        r"\{\{\s*(?:WP:)?RSPSTATUS\s*\|\s*([a-z]+)"
        r"|\{\{\s*(?:WP:)?RSPUSES\s*\|([^}]*)\}\}",
        wikitext,
    )
    rows: list[tuple[str, list[str]]] = []
    pending_status: str | None = None
    for status, uses in tokens:
        if status:
            pending_status = status
        elif pending_status is not None:
            domains = [
                d.strip().lower()
                for d in uses.split("|")
                if _is_domain(d.strip().lower())
            ]
            rows.append((pending_status, domains))
            pending_status = None
    return rows


def _rsp_subpages() -> list[str]:
    """Subpages the main RSP page actually transcludes, in order. Drives the
    fetch from the live page so a future split (e.g. a real /9) is picked up
    and redirect/draft pages that aren't transcluded are ignored."""
    main = json.loads(_fetch(_RSP_API.format(page=_RSP_MAIN)))["parse"]["wikitext"]["*"]
    nums = sorted({int(m) for m in re.findall(
        r"Reliable sources/Perennial sources/(\d+)", main)})
    return [f"{_RSP_MAIN}/{n}" for n in nums]


def _fetch_rsp() -> dict:
    by_tier: dict[str, set[str]] = {"reputable-news": set(), "low-quality": set()}
    revids: dict[str, int] = {}
    skipped = 0
    for page in _rsp_subpages():
        time.sleep(1)  # be polite to the MediaWiki API
        parse = json.loads(_fetch(_RSP_API.format(page=page)))["parse"]
        revids[page.rsplit("/", 1)[1]] = parse["revid"]
        for status, domains in _parse_rsp_rows(parse["wikitext"]["*"]):
            tier = _RSP_STATUS_TO_TIER.get(status)
            if tier is None:
                if status not in _RSP_STATUS_SKIP:
                    print(f"  warn: unmapped RSP status {status!r}", file=sys.stderr)
                skipped += len(domains)
                continue
            by_tier[tier].update(domains)
    # A domain can be both gr and gu across entries (e.g. a publisher's news vs.
    # opinion arm); treat any unreliable listing as decisive -> low-quality.
    by_tier["reputable-news"] -= by_tier["low-quality"]
    print(f"  RSP: {len(revids)} subpages, "
          f"{len(by_tier['reputable-news'])} reputable, "
          f"{len(by_tier['low-quality'])} low-quality, "
          f"{skipped} skipped (no-consensus/marginal)")
    return {
        "provenance": {
            "name": "Wikipedia:Reliable sources/Perennial sources",
            "url": RSP_PAGE_URL,
            "subpage_revids": revids,
            "status_mapping": _RSP_STATUS_TO_TIER,
            "skipped_status_codes": sorted(_RSP_STATUS_SKIP),
        },
        "tier_source": "wikipedia-rsp",
        "rationales": {
            "reputable-news": "Wikipedia RSP: generally reliable.",
            "low-quality": "Wikipedia RSP: generally unreliable, deprecated, or blacklisted.",
        },
        "domains": {t: sorted(s) for t, s in by_tier.items()},
    }


def _build(now_iso: str) -> dict:
    print("Fetching IFCN signatories...", file=sys.stderr)
    ifcn = _fetch_ifcn()
    print(f"  IFCN: {len(ifcn['domains'])} domains", file=sys.stderr)
    print("Fetching Wikipedia RSP...", file=sys.stderr)
    rsp = _fetch_rsp()
    editorial = {
        "provenance": {
            "name": "Project editorial supplement",
            "note": (
                "Hand-curated by the project for tiers with no clean public "
                "feed (primary-source, satirical) and gap-fillers. Applied "
                "only where IFCN / Wikipedia RSP do not already classify the "
                "domain. tier_source is recorded as 'editorial-curated'."
            ),
        },
        "tier_source": "editorial-curated",
        "domains": {t: sorted(set(d)) for t, d in _EDITORIAL.items()},
    }
    non_evidence_flat = sorted({d for cat in _NON_EVIDENCE.values() for d in cat})
    non_evidence = {
        "provenance": {
            "name": "Project non-evidence exclusion list",
            "note": (
                "Domains that are not sources of factual claims (stock-image "
                "marketplaces, shopping, UGC content farms). Dropped before "
                "classification so they never enter source_quality_table or "
                "reach reconcile. Editorial — there is no public feed for this. "
                "Major social/video platforms are deliberately NOT excluded."
            ),
            "categories": {c: sorted(set(d)) for c, d in _NON_EVIDENCE.items()},
        },
        "domains": non_evidence_flat,
    }
    print(f"  non-evidence: {len(non_evidence_flat)} domains", file=sys.stderr)
    return {
        "generated_at": now_iso,
        "generator": "scripts/refresh_source_lists.py",
        "precedence": ["ifcn", "wikipedia_rsp", "editorial"],
        "sources": {
            "ifcn": ifcn, "wikipedia_rsp": rsp, "editorial": editorial,
            "non_evidence": non_evidence,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fetch and compare; exit 1 if domains changed (no write)")
    args = ap.parse_args()

    now_iso = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    data = _build(now_iso)

    if args.check:
        if not OUTPUT_PATH.exists():
            print("source_lists.json missing", file=sys.stderr)
            return 1
        old = json.loads(OUTPUT_PATH.read_text())

        def _domsig(d):  # compare domains only, ignore timestamps/revids
            return json.dumps(
                {k: v["domains"] for k, v in d["sources"].items()},
                sort_keys=True,
            )

        if _domsig(old) != _domsig(data):
            print("STALE: source domains differ from upstream. Run without --check.",
                  file=sys.stderr)
            return 1
        print("up to date.")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    s = data["sources"]
    total = (len(s["ifcn"]["domains"])
             + sum(len(v) for v in s["wikipedia_rsp"]["domains"].values())
             + sum(len(v) for v in s["editorial"]["domains"].values())
             + len(s["non_evidence"]["domains"]))
    print(f"Wrote {OUTPUT_PATH} ({total} domains across all blocks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
