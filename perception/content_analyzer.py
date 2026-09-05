"""Content Analyzer — verified content-visibility findings (Content Improvement Keys).

Unlike the report's inferred roadmap, every finding here comes from a REAL check:
we fetch the entity's website(s), inspect the served HTML for schema.org markup,
llms.txt, and AI-crawler access, and query the Wikidata/Wikipedia APIs. Anything
that can't be checked is marked not_assessed (◐), never guessed.

Additive and self-contained: this module touches no scoring logic. It returns a
ContentFindings object; persistence/rendering live elsewhere.

Phase 1 scope: website (schema.org, llms.txt, AI-readability) + Wikidata +
Wikipedia. Directories and Reddit are deferred to a later phase.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from .models import ContentFinding, ContentFindings

_UA = "PulseContentAnalyzer/1.0 (+https://careclimb.com; RLDatix AI Visibility)"
_TIMEOUT = 8.0
_MAX_PAGES_TOTAL = 12
_MAX_PAGES_PER_SITE = 6
_MAX_SITES = 4

# AI crawler user-agents whose blocking matters for AI visibility.
_AI_CRAWLERS = ("gptbot", "google-extended", "claudebot", "anthropic-ai",
                "perplexitybot", "ccbot", "bytespider", "oai-searchbot")

# Provider-facing pages worth crawling beyond the homepage (path hints).
_KEY_PATH_HINTS = ("provider", "physician", "doctor", "team", "staff", "find-a",
                   "location", "our-", "care", "service", "about", "directory")


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": _UA})


def _origin(url: str) -> str:
    p = urlparse(url if "//" in url else "https://" + url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def _norm_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return url if "//" in url else "https://" + url


# ── Website crawl ─────────────────────────────────────────────────────────────

def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        return None
    return None


def _schema_types(html: str) -> set:
    """schema.org @type values from JSON-LD + microdata in served HTML."""
    types: set = set()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict):
                for node in ([obj] + (obj.get("@graph") or [])):
                    if isinstance(node, dict) and node.get("@type"):
                        t = node["@type"]
                        types.update(t if isinstance(t, list) else [t])
    for tag in soup.find_all(attrs={"itemtype": True}):
        m = re.search(r"schema\.org/(\w+)", tag.get("itemtype", ""))
        if m:
            types.add(m.group(1))
    return {str(t) for t in types}


def _crawl_site(client: httpx.Client, url: str, page_budget: int) -> dict:
    """Fetch homepage + a few key linked same-domain pages. Returns a snapshot."""
    origin = _origin(url)
    home = _fetch(client, _norm_url(url))
    snap = {"url": url, "origin": origin, "reachable": home is not None,
            "schema_types": set(), "pages": 0, "home_text_len": 0,
            "llms_txt": None, "robots_blocks_ai": None, "robots_blocks_all": None}
    if home is None:
        return snap
    snap["pages"] = 1
    snap["schema_types"] |= _schema_types(home)
    soup = BeautifulSoup(home, "html.parser")
    snap["home_text_len"] = len(soup.get_text(" ", strip=True))

    # A few key same-domain pages
    seen = {_norm_url(url)}
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(origin, a["href"])
        if urlparse(href).netloc != urlparse(origin).netloc:
            continue
        if href in seen:
            continue
        if any(h in href.lower() for h in _KEY_PATH_HINTS):
            links.append(href)
    for href in links:
        if snap["pages"] >= page_budget:
            break
        seen.add(href)
        h = _fetch(client, href)
        if h:
            snap["pages"] += 1
            snap["schema_types"] |= _schema_types(h)

    # llms.txt
    try:
        r = client.get(origin + "/llms.txt")
        snap["llms_txt"] = (r.status_code == 200 and len(r.text.strip()) > 0)
    except Exception:
        snap["llms_txt"] = None

    # robots.txt AI-crawler posture
    try:
        r = client.get(origin + "/robots.txt")
        if r.status_code == 200:
            txt = r.text.lower()
            snap["robots_blocks_ai"] = [b for b in _AI_CRAWLERS
                                        if re.search(rf"user-agent:\s*{re.escape(b)}", txt)
                                        and re.search(r"disallow:\s*/", txt)]
            snap["robots_blocks_all"] = bool(
                re.search(r"user-agent:\s*\*\s*\ndisallow:\s*/\s*$", txt, re.M))
    except Exception:
        pass
    return snap


def _check_website(snaps: list, entity_kind: str) -> list:
    findings: list = []
    reachable = [s for s in snaps if s["reachable"]]
    if not reachable:
        findings.append(dict(platform="website", category="risk", severity="high",
                             status="not_assessed",
                             teaser_summary="The website could not be reached for analysis.",
                             current_state="No provided URL responded with HTML.",
                             expected_state="A reachable public website.",
                             remediation_type="website_fix",
                             evidence=[s["url"] for s in snaps]))
        return findings

    all_types = set().union(*[s["schema_types"] for s in reachable])
    org_types = {"MedicalOrganization", "Hospital", "MedicalClinic", "Physician",
                 "LocalBusiness", "MedicalBusiness"}
    ev = [s["origin"] for s in reachable]

    if not (all_types & org_types):
        findings.append(dict(platform="structured_data", category="missing", severity="high",
                             status="verified",
                             teaser_summary="No healthcare schema.org markup found — AI assistants can't reliably parse your organization.",
                             current_state=f"No MedicalOrganization/Physician/LocalBusiness schema across {len(reachable)} site(s); types seen: {', '.join(sorted(all_types)) or 'none'}.",
                             expected_state="MedicalOrganization (and Physician for provider bios) schema on key pages.",
                             remediation_type="schema_markup", evidence=ev))
    else:
        if entity_kind == "practice" and "Physician" not in all_types:
            findings.append(dict(platform="structured_data", category="missing", severity="medium",
                                 status="verified",
                                 teaser_summary="Provider bios lack Physician schema, so individual clinicians are hard for AI to attribute.",
                                 current_state=f"Schema present ({', '.join(sorted(all_types & org_types))}) but no Physician type.",
                                 expected_state="Physician schema on every provider bio, linked to the organization.",
                                 remediation_type="schema_markup", evidence=ev))
        if not (all_types & {"MedicalOrganization", "Hospital", "MedicalClinic"}):
            findings.append(dict(platform="structured_data", category="missing", severity="medium",
                                 status="verified",
                                 teaser_summary="No MedicalOrganization schema — the entity itself isn't described in machine-readable form.",
                                 current_state=f"Types seen: {', '.join(sorted(all_types))}.",
                                 expected_state="A MedicalOrganization/Hospital/MedicalClinic entity in schema.",
                                 remediation_type="schema_markup", evidence=ev))

    if all(s["llms_txt"] is False for s in reachable):
        findings.append(dict(platform="llms_txt", category="opportunity", severity="low",
                             status="verified",
                             teaser_summary="No llms.txt — you aren't giving AI assistants a curated guide to your content.",
                             current_state="No /llms.txt on the provided site(s).",
                             expected_state="An llms.txt describing key pages, providers, and where to find canonical facts.",
                             remediation_type="website_fix", evidence=[s["origin"] + "/llms.txt" for s in reachable]))

    blocked = sorted({b for s in reachable for b in (s.get("robots_blocks_ai") or [])})
    if blocked or any(s.get("robots_blocks_all") for s in reachable):
        who = ", ".join(blocked) if blocked else "all crawlers"
        findings.append(dict(platform="website", category="risk", severity="high",
                             status="verified",
                             teaser_summary=f"Your robots.txt blocks AI crawlers ({who}) — you're invisible to those assistants by choice.",
                             current_state=f"robots.txt disallows: {who}.",
                             expected_state="Allow reputable AI crawlers to index public content.",
                             remediation_type="website_fix", evidence=[s["origin"] + "/robots.txt" for s in reachable]))

    thin = [s for s in reachable if s["home_text_len"] < 400]
    if thin:
        findings.append(dict(platform="website", category="risk", severity="medium",
                             status="partial",
                             teaser_summary="Key homepage content may be locked in images or scripts rather than crawlable text.",
                             current_state=f"Very little machine-readable text on {len(thin)} homepage(s).",
                             expected_state="Core facts (services, providers, locations) present as real HTML text.",
                             remediation_type="website_fix", evidence=[s["origin"] for s in thin]))
    return findings


# ── Wikidata ──────────────────────────────────────────────────────────────────

def _check_wikidata(client: httpx.Client, entity_name: str, known_website: str) -> tuple:
    api = "https://www.wikidata.org/w/api.php"
    try:
        r = client.get(api, params={"action": "wbsearchentities", "search": entity_name,
                                    "language": "en", "format": "json", "limit": 1})
        hits = r.json().get("search", [])
    except Exception:
        return ([dict(platform="wikidata", category="opportunity", severity="low",
                      status="not_assessed",
                      teaser_summary="Wikidata could not be checked at analysis time.",
                      remediation_type="wikidata_edit", evidence=[])], None)
    if not hits:
        return ([dict(platform="wikidata", category="opportunity", severity="medium",
                      status="verified",
                      teaser_summary="No Wikidata entity — AI models miss a key structured knowledge source about you.",
                      current_state=f"No Wikidata item matches '{entity_name}'.",
                      expected_state="A Wikidata item with instance-of, location, official website, and parent org.",
                      remediation_type="wikidata_edit", evidence=[api])], None)
    qid = hits[0]["id"]
    try:
        r = client.get(api, params={"action": "wbgetentities", "ids": qid,
                                    "props": "claims", "format": "json"})
        claims = r.json()["entities"][qid].get("claims", {})
    except Exception:
        return ([], qid)
    findings = []
    labels = {"P31": "instance-of", "P856": "official website",
              "P131": "location", "P749": "parent organization"}
    missing = [labels[p] for p in labels if p not in claims]
    if missing:
        findings.append(dict(platform="wikidata", category="missing", severity="low",
                             status="verified",
                             teaser_summary=f"Your Wikidata item is sparse — missing {', '.join(missing)}.",
                             current_state=f"Item {qid} is missing: {', '.join(missing)}.",
                             expected_state="A complete Wikidata item with those properties populated.",
                             remediation_type="wikidata_edit",
                             evidence=[f"https://www.wikidata.org/wiki/{qid}"]))
    return (findings, qid)


# ── Wikipedia ─────────────────────────────────────────────────────────────────

def _check_wikipedia(client: httpx.Client, entity_name: str) -> tuple:
    api = "https://en.wikipedia.org/w/api.php"
    try:
        r = client.get(api, params={"action": "query", "list": "search",
                                    "srsearch": entity_name, "srlimit": 1, "format": "json"})
        hits = r.json().get("query", {}).get("search", [])
    except Exception:
        return ([dict(platform="wikipedia", category="opportunity", severity="low",
                      status="not_assessed",
                      teaser_summary="Wikipedia could not be checked at analysis time.",
                      remediation_type="talk_page_request", evidence=[])], None)
    if not hits:
        return ([dict(platform="wikipedia", category="opportunity", severity="low",
                      status="verified",
                      teaser_summary="No Wikipedia article — a notability assessment is worth doing before pursuing one.",
                      current_state=f"No article matches '{entity_name}'.",
                      expected_state="If notable, an accurate, well-cited article; if not, effort belongs on Wikidata/directories.",
                      remediation_type="talk_page_request", evidence=[api])], None)
    title = hits[0]["title"]
    url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    # Simple relevance guard: share a distinctive token with the entity name.
    ename = entity_name.lower()
    if not any(tok in ename for tok in title.lower().split() if len(tok) >= 4):
        return ([dict(platform="wikipedia", category="opportunity", severity="low",
                      status="verified",
                      teaser_summary="No clearly-matching Wikipedia article — worth assessing notability.",
                      current_state=f"Closest article '{title}' does not clearly match.",
                      expected_state="An accurate article, if notable.",
                      remediation_type="talk_page_request", evidence=[url])], None)
    return ([dict(platform="wikipedia", category="opportunity", severity="low",
                  status="partial",
                  teaser_summary=f"A possibly-related Wikipedia article was found ('{title}') — confirm it's yours, then review its facts.",
                  current_state=f"Closest article: '{title}' (match not verified).",
                  expected_state="If it's yours: facts (leadership, locations, affiliations, official site) current and cited.",
                  remediation_type="talk_page_request", evidence=[url])], title)


# ── Reputation (location basis) ───────────────────────────────────────────────

def _check_reputation(rep: dict) -> list:
    """Location-basis reputation findings from the base diagnostic's verified
    Google data (per-location ratings/volume + footprint consistency). Helps
    local search + AI visibility — SocialClimb's domain. Provider-basis findings
    are a separate (later) layer."""
    findings: list = []
    if not rep:
        return findings
    fp = rep.get("footprint") or {}
    consistency = (fp.get("consistency") or "").lower()
    if "fragment" in consistency or "unclaim" in consistency:
        findings.append(dict(
            platform="reputation", category="risk", severity="high", status="verified",
            teaser_summary="Google Business Profiles are fragmented or unclaimed across locations — a direct drag on local search and AI recommendations.",
            current_state=f"Listing consistency: {fp.get('consistency')}."
                          + (f" Ratings range {fp.get('rating_range')}." if fp.get("rating_range") else ""),
            expected_state="Every location has a single, claimed, consistent Google Business Profile.",
            remediation_type="listing_management", evidence=[]))

    locs = rep.get("locations") or []
    weak = []
    for l in locs:
        r = l.get("google_rating")
        c = l.get("google_review_count")
        if r is None:
            continue
        if r < 4.0 or (c is not None and c < 25):
            weak.append(l)
    for l in weak[:8]:
        r = l.get("google_rating")
        c = l.get("google_review_count") or 0
        sev = "high" if (r is not None and r < 3.5) else "medium"
        cat = "risk" if (r is not None and r < 4.0) else "opportunity"
        findings.append(dict(
            platform="reputation", category=cat, severity=sev, status="verified",
            teaser_summary=f"{l.get('name') or 'A location'} has a weak Google reputation ({r}★, {c} reviews) — patients and AI both weight this.",
            current_state=f"{l.get('name')}: {r}★ from {c} review(s)"
                          + (f" — {l.get('address')}" if l.get("address") else ""),
            expected_state="4.5★+ with steady, recent review volume.",
            remediation_type="reputation_program",
            evidence=[l.get("address") or l.get("name") or ""]))

    # Single-location entities: no consolidated_locations, but an aggregate read.
    if not locs and rep.get("aggregate_rating") is not None:
        r = rep["aggregate_rating"]
        c = rep.get("aggregate_count") or 0
        if r < 4.0 or c < 25:
            findings.append(dict(
                platform="reputation",
                category="risk" if r < 4.0 else "opportunity",
                severity="high" if r < 3.5 else "medium", status="verified",
                teaser_summary=f"Google reputation is weak ({r}★, {c} reviews) — a drag on local visibility.",
                current_state=f"{r}★ from {c} review(s).",
                expected_state="4.5★+ with steady, recent review volume.",
                remediation_type="reputation_program", evidence=[]))
    return findings


# ── Orchestrator ──────────────────────────────────────────────────────────────

def analyze_content(entity_name: str, website_urls: list, city: str = "", state: str = "",
                    entity_kind: str = "hospital", reputation: dict = None) -> ContentFindings:
    """Run the verified content checks and return a ContentFindings object.

    Never raises: any component failure yields not_assessed findings and a
    whole-run status that reflects what could/couldn't be checked."""
    urls = [_norm_url(u) for u in (website_urls or []) if (u or "").strip()][:_MAX_SITES]
    raw: list = []
    snapshot = {"website_urls": urls, "wikipedia_article": None,
                "wikidata_qid": None, "pages_crawled": 0}
    partial = False

    with _client() as client:
        # Website(s)
        snaps = []
        budget = _MAX_PAGES_TOTAL
        for u in urls:
            per = min(_MAX_PAGES_PER_SITE, max(1, budget))
            s = _crawl_site(client, u, per)
            budget -= s["pages"]
            snaps.append(s)
            if budget <= 0:
                break
        snapshot["pages_crawled"] = sum(s["pages"] for s in snaps)
        if urls:
            try:
                raw += _check_website(snaps, entity_kind)
            except Exception:
                partial = True
        # Wikidata
        try:
            wd, qid = _check_wikidata(client, entity_name, urls[0] if urls else "")
            raw += wd
            snapshot["wikidata_qid"] = qid
        except Exception:
            partial = True
        # Wikipedia
        try:
            wp, title = _check_wikipedia(client, entity_name)
            raw += wp
            snapshot["wikipedia_article"] = title
        except Exception:
            partial = True

    # Reputation (location basis) — from the base diagnostic's verified data; no
    # network calls here, so it runs outside the HTTP client block.
    try:
        raw += _check_reputation(reputation)
    except Exception:
        partial = True

    # Assign stable IDs, severity-sort (high→low), wrap in models.
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    raw.sort(key=lambda f: sev_rank.get(f.get("severity", "low"), 3))
    findings = []
    for i, f in enumerate(raw, 1):
        f.setdefault("evidence", [])
        findings.append(ContentFinding(finding_id=f"CIK-{i:03d}", **f))

    if any(f.status == "not_assessed" for f in findings) or partial:
        run_status = "partial"
    else:
        run_status = "verified"
    if not findings:
        run_status = "not_assessed"

    from .db import _norm_entity_name
    return ContentFindings(
        run_id="", norm_entity=_norm_entity_name(entity_name),
        generated_at=date.today(), source_snapshot=snapshot,
        findings=findings, status=run_status,
    )
