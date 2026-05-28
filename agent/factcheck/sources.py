"""Source-reliability lookup (design §4.5.2).

Two phases per URL:
  1. Curated table (IFCN signatories, Wikipedia RSP "generally reliable" /
     "primary source" / "satirical" / "generally unreliable", plus
     subdomain → parent fallback). Each hit's `tier_source` records which
     list it came from (ifcn, wikipedia-rsp, etc.) — the design's separate
     IFCN / Wikipedia / MBFC steps collapse to a single lookup here.
  2. Claude model-prior classifier for everything else, batched per
     pipeline invocation and cached for the process lifetime.

Failure to classify falls through to `unknown`.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional
from urllib.parse import urlparse

from agent.shared.text import canonicalize_url

from .schema import SourceQualityEntry, SourceTier, TierSource


logger = logging.getLogger(__name__)


# ── Curated tier sets ────────────────────────────────────────────────────────


# IFCN signatories + nationally-recognized fact-checkers. Selection biased
# toward outlets we expect Bing to surface for English-language claims.
_FACT_CHECKER_DOMAINS: set[str] = {
    "snopes.com",
    "factcheck.org",
    "politifact.com",
    "leadstories.com",
    "checkyourfact.com",
    "fullfact.org",
    "factcheckni.org",
    "logicallyfacts.com",
    "factcheck.afp.com",
    "factual.afp.com",
    "factuel.afp.com",
    "thequint.com",
    "vishvasnews.com",
    "boomlive.in",
    "altnews.in",
    "factly.in",
    "newschecker.in",
    "factcrescendo.com",
    "fact-crescendo.com",
    "africacheck.org",
    "pesacheck.org",
    "dubawa.org",
    "namibiafactcheck.org.na",
    "verafiles.org",
    "rappler.com",
    "tjekdet.dk",
    "faktisk.no",
    "correctiv.org",
    "dpa-factchecking.com",
    "demagog.org.pl",
    "factcheck.kz",
    "stopfake.org",
    "maldita.es",
    "newtral.es",
    "efe.com",
    "facta.news",
    "pagellapolitica.it",
    "lavoce.info",
    "20minutes.fr",
    "checkdesk.org",
    "checkpoint.bg",
}


# Wikipedia RSP "generally reliable" — major newspapers/broadcasters with
# clear editorial standards and a track record of corrections. Not exhaustive.
_REPUTABLE_NEWS_DOMAINS: set[str] = {
    # United States — general newspapers + wires
    "apnews.com", "reuters.com", "nytimes.com", "washingtonpost.com",
    "wsj.com", "usatoday.com", "latimes.com", "chicagotribune.com",
    "bostonglobe.com", "miamiherald.com", "houstonchronicle.com",
    "tampabay.com",
    "propublica.org", "themarshallproject.org",
    # Broadcasters & magazines
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
    "elpais.com", "elmundo.es",
    "corriere.it", "lastampa.it", "repubblica.it",
    # Australia / NZ
    "smh.com.au", "theage.com.au", "abc.net.au", "theguardian.com.au",
    "rnz.co.nz", "stuff.co.nz",
    # Canada
    "globeandmail.com", "cbc.ca", "nationalpost.com", "thestar.com",
    # India
    "thehindu.com", "indianexpress.com", "hindustantimes.com",
    "timesofindia.indiatimes.com", "ndtv.com", "scroll.in",
    "thewire.in",
    # Middle East
    "haaretz.com", "timesofisrael.com", "aljazeera.com",
    "thenationalnews.com",
    # East Asia
    "japantimes.co.jp", "asahi.com", "nhk.or.jp",
    "scmp.com", "straitstimes.com",
}


# Primary-source-tier — government, IGO, academic, journals, institutions.
_PRIMARY_SOURCE_DOMAINS: set[str] = {
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
    "canada.ca", "gc.ca",
    "gov.au", "gov.nz",
    "india.gov.in", "rbi.org.in",
    # IGOs
    "who.int", "un.org", "imf.org", "worldbank.org", "oecd.org",
    "wto.org", "unesco.org", "unicef.org", "ilo.org", "wmo.int",
    "icrc.org", "iea.org", "iaea.org", "nato.int",
    # Academic / Journals
    "nature.com", "science.org", "thelancet.com", "nejm.org",
    "jamanetwork.com", "bmj.com", "cell.com", "plos.org",
    "pnas.org", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "arxiv.org", "biorxiv.org", "medrxiv.org",
    "smithsonianmag.com", "nationalgeographic.com",
    "scientificamerican.com",
}


_SATIRICAL_DOMAINS: set[str] = {
    "theonion.com", "babylonbee.com", "worldnewsdailyreport.com",
    "clickhole.com", "thehardtimes.net", "reductress.com",
    "thedailymash.co.uk", "thebeaverton.com", "thepoke.co.uk",
    "the-postillon.com", "newsthump.com", "thespoof.com",
    "empirenews.net", "now8news.com", "huzlers.com",
    "newsbiscuit.com", "private-eye.co.uk", "thedailywtf.com",
    "rocketnewsfeed.com", "thestonecuttersjournal.com",
}


_LOW_QUALITY_DOMAINS: set[str] = {
    # Aggregators without editorial oversight
    "worldrecordacademy.org",
    # Wikipedia RSP "generally unreliable" or worse
    "dailymail.co.uk", "dailycaller.com",
    "naturalnews.com", "infowars.com", "prisonplanet.com",
    "beforeitsnews.com", "yournewswire.com", "newspunch.com",
    "gateway-pundit.com", "thegatewaypundit.com",
    "zerohedge.com", "rt.com", "sputniknews.com", "sputnikglobe.com",
    "presstv.ir", "geopolitics.news",
    "globalresearch.ca", "veteranstoday.com",
    "thefreethoughtproject.com", "activistpost.com",
    "wnd.com", "newsmax.com", "oann.com",
}


_TIER_BY_DOMAIN: dict[str, tuple[SourceTier, TierSource, str]] = {}
for _d in _FACT_CHECKER_DOMAINS:
    _TIER_BY_DOMAIN[_d] = ("fact-checker", "ifcn", "IFCN signatory or recognized fact-checker.")
for _d in _REPUTABLE_NEWS_DOMAINS:
    _TIER_BY_DOMAIN[_d] = (
        "reputable-news", "wikipedia-rsp",
        "Wikipedia perennial sources / RSP: generally reliable.",
    )
for _d in _PRIMARY_SOURCE_DOMAINS:
    _TIER_BY_DOMAIN[_d] = (
        "primary-source", "wikipedia-rsp",
        "Primary source (government, institution, peer-reviewed journal).",
    )
for _d in _SATIRICAL_DOMAINS:
    _TIER_BY_DOMAIN[_d] = (
        "satirical", "wikipedia-rsp",
        "Self-described or well-known satirical/fictional site.",
    )
for _d in _LOW_QUALITY_DOMAINS:
    _TIER_BY_DOMAIN[_d] = (
        "low-quality", "wikipedia-rsp",
        "Wikipedia perennial sources or community consensus: unreliable.",
    )


# ── Model-prior classifier (Stage 4 of the four-step lookup) ────────────────


_MODEL_PRIOR_CACHE: dict[str, tuple[SourceTier, str]] = {}
_MODEL_PRIOR_LOCK = threading.Lock()

# Tables-backed persistent cache so a Claude classification for one
# domain doesn't have to repeat on every worker / pod restart. Set up
# lazily on first miss; failures (Tables unreachable, env not set)
# silently fall back to in-memory-only.
_PERSISTENT_TABLE_NAME = "SourceTierCache"
_PERSISTENT_PARTITION = "domains"
_persistent_table_uninit = object()
_persistent_table_lock = threading.Lock()


def _get_persistent_table():
    """Lazy Tables client for SourceTierCache. Returns None when not configured."""
    global _persistent_table_client
    if _persistent_table_client is _persistent_table_uninit:
        # First call: try to initialize.
        with _persistent_table_lock:
            if _persistent_table_client is _persistent_table_uninit:
                _persistent_table_client = _init_persistent_table()
    return _persistent_table_client


def _init_persistent_table():
    import os
    backend = os.getenv("DERAD_SOURCE_CACHE_BACKEND", "tables").lower()
    if backend != "tables":
        return None
    endpoint = os.getenv("DERAD_TABLES_ENDPOINT")
    if not endpoint:
        return None
    try:
        from azure.core.exceptions import ResourceExistsError
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential
        svc = TableServiceClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
            connection_timeout=10,
            read_timeout=15,
        )
        try:
            svc.create_table(_PERSISTENT_TABLE_NAME)
            logger.info("Created %s table", _PERSISTENT_TABLE_NAME)
        except ResourceExistsError:
            pass
        return svc.get_table_client(_PERSISTENT_TABLE_NAME)
    except Exception:
        logger.warning(
            "SourceTierCache init failed; running with in-memory-only cache.",
            exc_info=True,
        )
        return None


_persistent_table_client = _persistent_table_uninit


def _persistent_get(domain: str) -> Optional[tuple[SourceTier, str]]:
    tbl = _get_persistent_table()
    if tbl is None:
        return None
    try:
        ent = tbl.get_entity(_PERSISTENT_PARTITION, domain)
    except Exception:
        return None
    tier = ent.get("tier")
    rationale = ent.get("rationale") or ""
    if not tier:
        return None
    return tier, rationale


def _persistent_put(domain: str, tier: SourceTier, rationale: str) -> None:
    tbl = _get_persistent_table()
    if tbl is None:
        return
    try:
        tbl.upsert_entity({
            "PartitionKey": _PERSISTENT_PARTITION,
            "RowKey": domain,
            "tier": tier,
            "rationale": rationale,
        })
    except Exception:
        logger.warning("SourceTierCache upsert failed for %r", domain, exc_info=True)


_MODEL_PRIOR_SYSTEM = """You classify web-domain reliability for a fact-checking pipeline. For each input domain, decide ONE tier from this exact set:

- "fact-checker" — IFCN signatory or nationally-recognized fact-checking organisation (Snopes, FactCheck.org, Politifact, AFP Fact Check, AltNews, etc.).
- "reputable-news" — major newspaper/broadcaster/magazine with clear editorial standards and a track record of corrections (AP, Reuters, BBC, NYT, Guardian, Le Monde, Spiegel, etc.).
- "primary-source" — government, IGO, academic institution, peer-reviewed journal, or major institutional publisher (whitehouse.gov, who.int, nature.com, nih.gov, etc.).
- "aggregator" — content aggregator without significant original reporting (news.yahoo.com, news.google.com, etc.).
- "low-quality" — outlet community-consensus marked as unreliable, conspiracy, partisan-propaganda, or known to recycle hoaxes (naturalnews, infowars, gateway pundit, RT, Sputnik, etc.).
- "satirical" — self-described or widely-recognized satirical / fictional news site (The Onion, Babylon Bee, World News Daily Report, etc.).
- "unknown" — you have no confident prior on this domain.

For each domain, give a one-line rationale. Be conservative — if you're not sure, return "unknown".

Output a JSON object: a single top-level field `classifications` mapping each input domain → {tier, rationale}.
"""


def _classify_via_model_batch(domains: list[str]) -> dict[str, tuple[SourceTier, str]]:
    """Classify a batch of unknown domains. Layered cache:
      1. In-memory per-worker dict (fastest).
      2. Persistent Azure Tables row (fleet-wide; survives restarts).
      3. Single Claude call for anything still unknown; results write
         back to both caches.
    """
    if not domains:
        return {}

    # Layer 1 — in-memory cache.
    result: dict[str, tuple[SourceTier, str]] = {}
    fresh: list[str] = []
    with _MODEL_PRIOR_LOCK:
        for d in domains:
            cached = _MODEL_PRIOR_CACHE.get(d)
            if cached is not None:
                result[d] = cached
            else:
                fresh.append(d)
    if not fresh:
        return result

    # Layer 2 — persistent Tables cache. Populate the in-memory cache from
    # any hits so subsequent calls in this worker skip the lookup.
    still_unknown: list[str] = []
    for d in fresh:
        persisted = _persistent_get(d)
        if persisted is not None:
            with _MODEL_PRIOR_LOCK:
                _MODEL_PRIOR_CACHE[d] = persisted
            result[d] = persisted
        else:
            still_unknown.append(d)
    if not still_unknown:
        return result

    # Layer 3 — Claude classifier for the remaining domains.
    from pydantic import BaseModel

    class _Entry(BaseModel):
        tier: SourceTier
        rationale: str = ""

    class _BatchClassification(BaseModel):
        classifications: dict[str, _Entry]

    from .llm import call_claude_json
    user_prompt = json.dumps({"domains": sorted(set(still_unknown))}, indent=2)
    try:
        out = call_claude_json(
            prompt=user_prompt,
            schema=_BatchClassification,
            system=_MODEL_PRIOR_SYSTEM,
            reasoning_effort="low",
            max_tokens=2048,
            timeout=30.0,
        )
    except (ValueError, TimeoutError):
        logger.warning(
            "Model-prior classification failed; %d domains stay unknown.",
            len(still_unknown), exc_info=True,
        )
        return result

    with _MODEL_PRIOR_LOCK:
        for d, entry in out.classifications.items():
            tier = entry.tier
            rationale = entry.rationale or "Model parametric prior."
            _MODEL_PRIOR_CACHE[d] = (tier, rationale)
            result[d] = (tier, rationale)
            _persistent_put(d, tier, rationale)
    return result


# ── Public API ──────────────────────────────────────────────────────────────


def _normalize_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower()


def _registered_domain(host: str) -> str:
    """Strip www. and other common subdomain prefixes."""
    if not host:
        return ""
    if host.startswith("www."):
        return host[4:]
    return host


def _lookup_curated(host: str) -> tuple[SourceTier, TierSource, str] | None:
    """Check curated tables with subdomain → parent fallback."""
    if host in _TIER_BY_DOMAIN:
        return _TIER_BY_DOMAIN[host]
    # Strip subdomains one segment at a time and re-check.
    parts = host.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in _TIER_BY_DOMAIN:
            return _TIER_BY_DOMAIN[parent]
    return None


def classify_url(url: str) -> SourceQualityEntry:
    """Classify a single URL. Equivalent to build_quality_table([url])[0] but
    skips the batch optimization — prefer the batch entrypoint for multiple URLs."""
    canonical = canonicalize_url(url)
    host = _registered_domain(_normalize_domain(canonical))
    if not host:
        return SourceQualityEntry(
            url=canonical, tier="unknown", tier_source="model-prior",
            rationale="URL has no parseable host.",
        )
    hit = _lookup_curated(host)
    if hit:
        tier, tier_source, rationale = hit
        return SourceQualityEntry(url=canonical, tier=tier, tier_source=tier_source, rationale=rationale)
    classified = _classify_via_model_batch([host])
    if host in classified:
        tier, rationale = classified[host]
        return SourceQualityEntry(
            url=canonical, tier=tier, tier_source="model-prior", rationale=rationale,
        )
    return SourceQualityEntry(
        url=canonical, tier="unknown", tier_source="model-prior",
        rationale=f"No entry for {host!r}; model fallback also returned unknown.",
    )


def build_quality_table(urls: list[str]) -> list[SourceQualityEntry]:
    """Build the source_quality_table for a set of URLs, de-duplicated in
    input order. Unknown domains are batched into a single Claude call."""
    seen: set[str] = set()
    ordered_urls: list[str] = []
    for url in urls:
        canonical = canonicalize_url(url)
        if canonical not in seen:
            seen.add(canonical)
            ordered_urls.append(canonical)

    # First pass — curated lookup. Collect unknowns for batched model call.
    cached: dict[str, SourceQualityEntry] = {}
    unknown_hosts: list[str] = []
    host_by_url: dict[str, str] = {}
    for url in ordered_urls:
        host = _registered_domain(_normalize_domain(url))
        host_by_url[url] = host
        if not host:
            cached[url] = SourceQualityEntry(
                url=url, tier="unknown", tier_source="model-prior",
                rationale="URL has no parseable host.",
            )
            continue
        hit = _lookup_curated(host)
        if hit:
            tier, tier_source, rationale = hit
            cached[url] = SourceQualityEntry(
                url=url, tier=tier, tier_source=tier_source, rationale=rationale,
            )
        elif host not in unknown_hosts:
            unknown_hosts.append(host)

    # Second pass — single batched model call for unknowns.
    model_results: dict[str, tuple[SourceTier, str]] = {}
    if unknown_hosts:
        logger.info("source classifier: %d unknown host(s) → batch model call", len(unknown_hosts))
        model_results = _classify_via_model_batch(unknown_hosts)

    out: list[SourceQualityEntry] = []
    for url in ordered_urls:
        if url in cached:
            out.append(cached[url])
            continue
        host = host_by_url[url]
        if host in model_results:
            tier, rationale = model_results[host]
            out.append(SourceQualityEntry(
                url=url, tier=tier, tier_source="model-prior", rationale=rationale,
            ))
        else:
            out.append(SourceQualityEntry(
                url=url, tier="unknown", tier_source="model-prior",
                rationale=f"No entry for {host!r}; model fallback returned no classification.",
            ))
    return out
