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

# Realistic desktop-Chrome UA + timeout for the headless-browser fallback used
# when a site (e.g. behind Cloudflare) returns 403 to the plain HTTP client.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_BROWSER_TIMEOUT_MS = 20000     # per-navigation cap
_BROWSER_LAUNCH_MS = 20000      # cap the launch so it can never hang the job
_BROWSER_SETTLE_MS = 2000       # brief wait for a JS challenge to resolve
# Flags required to render a heavy external page reliably in a container
# (Cloud Run): --no-sandbox (no user namespaces) and --disable-dev-shm-usage
# (the default 64MB /dev/shm is too small and otherwise causes hangs/crashes).
_BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


class _BrowserFetcher:
    """Lazy headless-Chromium fallback for sites that block the plain HTTP client
    (Cloudflare/WAF 403 to non-JS clients). Chromium launches only on first use,
    so unblocked runs pay nothing; one browser is reused for the whole analysis.
    Once a page nav clears a JS challenge, the context holds clearance cookies, so
    text files (llms.txt/robots.txt) are fetched via the same context's request."""

    def __init__(self):
        self._pw = self._browser = self._ctx = None
        self._failed = False   # a launch failure disables further attempts this run

    def _ensure(self) -> bool:
        if self._ctx is not None:
            return True
        if self._failed:
            return False
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(args=_BROWSER_ARGS,
                                                     timeout=_BROWSER_LAUNCH_MS)
            self._ctx = self._browser.new_context(user_agent=_BROWSER_UA)
            return True
        except Exception:
            self._failed = True
            return False

    def fetch_html(self, url: str) -> "tuple[str | None, int | None]":
        """Return (html_or_None, http_status_or_None). status distinguishes a
        bot-block (e.g. 403) from a genuine connection failure (status None)."""
        if not self._ensure():
            return None, None
        page = None
        try:
            page = self._ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_TIMEOUT_MS)
            page.wait_for_timeout(_BROWSER_SETTLE_MS)   # allow a JS challenge to resolve
            status = resp.status if resp else None
            if resp and resp.status == 200:
                html = page.content()
                if "<html" in html.lower():
                    return html, status
            return None, status
        except Exception:
            return None, None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def fetch_text(self, url: str) -> "tuple[int, str] | None":
        """Fetch a text file via the browser context (reuses challenge cookies).
        Returns (status_code, body) or None if the context isn't open/failed."""
        if self._ctx is None:
            return None
        try:
            r = self._ctx.request.get(url, timeout=_BROWSER_TIMEOUT_MS)
            return r.status, r.text()
        except Exception:
            return None

    def fetch_rendered_html(self, url: str, settle_ms: int = 3500) -> "str | None":
        """Navigate a page and return its rendered HTML after client-side JS runs
        — for directory search pages whose result cards load dynamically. Fail-soft:
        returns None on block/timeout/launch failure (never raises)."""
        if not self._ensure():
            return None
        page = None
        try:
            page = self._ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=_BROWSER_TIMEOUT_MS)
            page.wait_for_timeout(settle_ms)

            def _usable(h: str) -> bool:
                low = h.lower()
                return ("<html" in low and len(h) > 3000
                        and not any(s in low for s in ("just a moment", "attention required",
                                                       "verify you are human",
                                                       "enable javascript and cookies")))
            # Return the rendered page if it genuinely loaded — even when the initial
            # response was a Cloudflare 403 challenge that JS then resolved. If it's
            # still showing an interstitial, give the JS challenge one longer chance.
            html = page.content()
            if _usable(html):
                return html
            page.wait_for_timeout(6000)
            html = page.content()
            return html if _usable(html) else None
        except Exception:
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    @property
    def active(self) -> bool:
        return self._ctx is not None

    def close(self) -> None:
        for closer in (getattr(self._browser, "close", None),
                       getattr(self._pw, "stop", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass
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

# HTTP statuses that indicate an up-but-blocking site (WAF / bot protection),
# as opposed to a connection failure (which surfaces as an exception / None).
_BLOCK_STATUSES = {401, 403, 406, 409, 429, 503}


def _fetch(client: httpx.Client, url: str, browser: "_BrowserFetcher | None" = None) -> str | None:
    try:
        r = client.get(url)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        pass
    # Plain HTTP failed or was blocked (e.g. Cloudflare 403) — try a real browser.
    if browser is not None:
        return browser.fetch_html(url)[0]
    return None


def _fetch_home(client: httpx.Client, url: str,
                browser: "_BrowserFetcher | None") -> "tuple[str | None, str]":
    """Fetch the homepage, returning (html_or_None, reason). reason is one of:
    'ok', 'blocked' (up but WAF/bot-blocks automated readers), 'unreachable'
    (connection failed / DNS / timeout), 'no_html' (responded but not HTML)."""
    http_status = None
    try:
        r = client.get(url)
        http_status = r.status_code
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text, "ok"
    except Exception:
        http_status = None   # connection-level failure

    if browser is not None:
        html, bstatus = browser.fetch_html(url)
        if html is not None:
            return html, "ok"
        if bstatus in _BLOCK_STATUSES or http_status in _BLOCK_STATUSES:
            return None, "blocked"
        if bstatus is not None or http_status is not None:
            return None, "no_html"     # got an HTTP response, just not usable HTML
        return None, "unreachable"     # neither client nor browser could connect

    if http_status in _BLOCK_STATUSES:
        return None, "blocked"
    if http_status is not None:
        return None, "no_html"
    return None, "unreachable"


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


def _crawl_site(client: httpx.Client, url: str, page_budget: int,
                browser: "_BrowserFetcher | None" = None) -> dict:
    """Fetch homepage + a few key linked same-domain pages. Returns a snapshot."""
    origin = _origin(url)
    home, reason = _fetch_home(client, _norm_url(url), browser)
    snap = {"url": url, "origin": origin, "reachable": home is not None,
            "fetch_status": reason,
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
        # Prefer the browser context's request when active (fast, reuses any
        # challenge cookies) so we never pay for a fresh page navigation per
        # sub-page; otherwise plain HTTP only (no per-page browser fallback).
        h = None
        if browser is not None and browser.active:
            res = browser.fetch_text(href)
            if res is not None and res[0] == 200 and "<html" in res[1].lower():
                h = res[1]
        else:
            h = _fetch(client, href)
        if h:
            snap["pages"] += 1
            snap["schema_types"] |= _schema_types(h)

    # Fetch a text file (llms.txt/robots.txt), preferring the browser context if
    # it's active so Cloudflare-protected sites return the real file, not a 403.
    def _get_text(path: str):
        if browser is not None and browser.active:
            res = browser.fetch_text(origin + path)
            if res is not None:
                return res
        try:
            r = client.get(origin + path)
            return r.status_code, r.text
        except Exception:
            return None

    # llms.txt
    res = _get_text("/llms.txt")
    if res is not None:
        code, body = res
        snap["llms_txt"] = (code == 200 and len(body.strip()) > 0)

    # robots.txt AI-crawler posture
    res = _get_text("/robots.txt")
    if res is not None:
        code, body = res
        if code == 200:
            txt = body.lower()
            snap["robots_blocks_ai"] = [b for b in _AI_CRAWLERS
                                        if re.search(rf"user-agent:\s*{re.escape(b)}", txt)
                                        and re.search(r"disallow:\s*/", txt)]
            snap["robots_blocks_all"] = bool(
                re.search(r"user-agent:\s*\*\s*\ndisallow:\s*/\s*$", txt, re.M))
    return snap


def _check_website(snaps: list, entity_kind: str) -> list:
    findings: list = []
    reachable = [s for s in snaps if s["reachable"]]
    if not reachable:
        reasons = {s.get("fetch_status") for s in snaps}
        if "blocked" in reasons:
            # The site is up but its WAF/bot protection turns away non-browser
            # clients — a genuine AI-visibility problem, since AI crawlers that
            # don't execute JavaScript get the same 403. This IS a verified finding.
            findings.append(dict(
                platform="website", category="risk", severity="high",
                status="verified",
                teaser_summary="Your website blocks automated readers — AI assistants likely can't crawl it either.",
                current_state=("The site is online but returns a bot-block (e.g. HTTP 403 from a "
                               "Cloudflare/WAF challenge) to clients that don't execute JavaScript. "
                               "AI crawlers (GPTBot, ClaudeBot, PerplexityBot, Google-Extended) are "
                               "turned away the same way, so your content can't be read or cited."),
                expected_state=("Public, non-sensitive pages are reachable by legitimate AI crawlers — "
                                "allow known AI user-agents through your WAF/bot-management rules while "
                                "keeping login, portal, and PHI paths protected."),
                remediation_type="website_fix",
                evidence=[s["url"] for s in snaps]))
        else:
            # Connection failed / DNS / timeout — the site may be down or the URL wrong.
            findings.append(dict(
                platform="website", category="risk", severity="high",
                status="not_assessed",
                teaser_summary="The website could not be reached for analysis.",
                current_state=("No provided URL responded (connection failed, timed out, or the domain "
                               "did not resolve). The site may be down or the URL may be incorrect."),
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

def _name_matches(entity_name: str, candidate: str) -> bool:
    """True if a search hit plausibly refers to this entity — it must share a
    distinctive (>=4-char) token with the entity name. Guards against a generic
    name (e.g. 'Summit Medical Group') matching an unrelated knowledge item."""
    ename = (entity_name or "").lower()
    return any(tok in ename for tok in (candidate or "").lower().split() if len(tok) >= 4)


def _check_wikidata(client: httpx.Client, entity_name: str, known_website: str,
                    entity_kind: str = "hospital") -> tuple:
    api = "https://www.wikidata.org/w/api.php"
    # For an ambulatory/specialty practice, absence of a Wikidata item is low
    # priority (most practices have none, and it's a minor AI-visibility lever
    # relative to directories/GBP); for a hospital/system it matters more.
    _absent_sev = "low" if entity_kind == "practice" else "medium"
    try:
        r = client.get(api, params={"action": "wbsearchentities", "search": entity_name,
                                    "language": "en", "format": "json", "limit": 1})
        hits = r.json().get("search", [])
    except Exception:
        return ([dict(platform="wikidata", category="opportunity", severity="low",
                      status="not_assessed",
                      teaser_summary="Wikidata could not be checked at analysis time.",
                      remediation_type="wikidata_edit", evidence=[])], None)
    # Require a plausible name match, or treat as "no item" — never adopt an
    # unrelated QID (which would also feed the drafting engine a wrong entity).
    if not hits or not _name_matches(entity_name, hits[0].get("label", "")):
        return ([dict(platform="wikidata", category="opportunity", severity=_absent_sev,
                      status="verified",
                      teaser_summary="No Wikidata entity — AI models miss a key structured knowledge source about you.",
                      current_state=f"No Wikidata item clearly matches '{entity_name}'.",
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

def _check_wikipedia(client: httpx.Client, entity_name: str,
                     entity_kind: str = "hospital") -> tuple:
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
    # Relevance guard: the article must share a distinctive token with the name.
    if not _name_matches(entity_name, title):
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
    local search + AI visibility — RLDatix Reputation Management's domain.
    Provider-basis findings are a separate (later) layer."""
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
    weak_total = len(weak)
    weak = weak[:12]                             # table shows at most 12 rows
    if len(weak) >= 2:
        # Group multiple weak locations into ONE finding — one review-generation
        # program applies to all of them, so it's drafted + printed once with a
        # per-facility table (meta.rows) instead of a near-identical page each.
        rows = [{"name": l.get("name") or "A location", "location": l.get("address") or "",
                 "rating": l.get("google_rating"),
                 "reviews": l.get("google_review_count") or 0} for l in weak]
        ratings = [x["rating"] for x in rows if x["rating"] is not None]
        rng = f" ratings {min(ratings):.1f}–{max(ratings):.1f}★." if ratings else ""
        cap = f" Showing the {len(rows)} lowest-rated." if weak_total > len(rows) else ""
        findings.append(dict(
            platform="reputation", category="risk", severity="high", status="verified",
            teaser_summary=f"{weak_total} locations have a weak Google reputation — patients and AI both weight this.",
            current_state=f"{weak_total} facilities are below target;{rng}{cap} See the per-location table.",
            expected_state="4.5★+ with steady, recent review volume at each location.",
            remediation_type="reputation_program",
            evidence=[r["location"] or r["name"] for r in rows],
            meta={"group": "reputation_program", "rows": rows}))
    elif len(weak) == 1:
        l = weak[0]
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


# ── Safety / quality (hospital-only) ──────────────────────────────────────────

def _check_safety(safety: dict) -> list:
    """Hospital safety-grade findings from the base diagnostic's verified quality
    data. A MISSING Leapfrog grade means patients and AI assistants see no
    independent safety signal — the remediation is to participate in the Leapfrog
    Hospital Survey. A low grade (D/F) or low CMS star is a visible negative
    signal. Hospitals only (Leapfrog does not grade practices)."""
    findings: list = []
    if not safety or safety.get("entity_kind") != "hospital":
        return findings

    def _g(x):
        return (x.get("leapfrog_grade") or "").strip().upper()

    locs = safety.get("locations") or []
    if locs:
        no_grade = [l for l in locs if not _g(l)]
        low_grade = [l for l in locs if _g(l) in ("D", "F")]
        if no_grade:
            names = [l.get("name") for l in no_grade if l.get("name")]
            findings.append(dict(
                platform="safety", category="opportunity",
                severity="high" if len(no_grade) >= 2 else "medium", status="verified",
                teaser_summary=f"{len(no_grade)} hospital(s) have no Leapfrog Hospital Safety Grade — a safety signal patients and AI assistants can't see.",
                current_state=(f"{len(no_grade)} of {len(locs)} facilities are not rated by The Leapfrog Group: "
                               + ", ".join(names[:10]) + ("…" if len(names) > 10 else "") + "."),
                expected_state="Every hospital participates in the Leapfrog Hospital Survey and publishes a current safety grade.",
                remediation_type="leapfrog_submission", evidence=names[:12]))
        if low_grade:
            names = [f"{l.get('name')} ({_g(l)})" for l in low_grade if l.get("name")]
            findings.append(dict(
                platform="safety", category="risk", severity="high", status="verified",
                teaser_summary=f"{len(low_grade)} hospital(s) carry a low Leapfrog safety grade (D/F) — a visible negative signal.",
                current_state="Graded D or F by The Leapfrog Group: " + ", ".join(names[:10]) + ("…" if len(names) > 10 else "") + ".",
                expected_state="An improving Leapfrog Hospital Safety Grade (C or better), with verified survey participation.",
                remediation_type="leapfrog_submission", evidence=names[:12]))
        low_star = [l for l in locs if isinstance(l.get("cms_star_rating"), int) and l.get("cms_star_rating") <= 2]
        if low_star:
            names = [f"{l.get('name')} ({l.get('cms_star_rating')}★)" for l in low_star if l.get("name")]
            findings.append(dict(
                platform="safety", category="opportunity",
                severity="high" if len(low_star) >= 2 else "medium", status="verified",
                teaser_summary=f"{len(low_star)} hospital(s) have a low CMS Overall Star Rating (≤2★) — a quality signal patients and AI assistants weight.",
                current_state="Low CMS Overall Hospital Quality Star Rating: " + ", ".join(names[:10]) + ("…" if len(names) > 10 else "") + ".",
                expected_state="Improved CMS measures (3★+); ensure each hospital's CMS Care Compare data is complete and current.",
                remediation_type="quality_improvement", evidence=names[:12]))
    else:
        g = _g(safety)
        name = safety.get("name") or "This hospital"
        if not g:
            findings.append(dict(
                platform="safety", category="opportunity", severity="medium", status="verified",
                teaser_summary="No Leapfrog Hospital Safety Grade — patients and AI assistants see no independent safety signal.",
                current_state=f"{name} is not currently rated by The Leapfrog Group.",
                expected_state="Participation in the Leapfrog Hospital Survey, with a published safety grade.",
                remediation_type="leapfrog_submission", evidence=[]))
        elif g in ("D", "F"):
            findings.append(dict(
                platform="safety", category="risk", severity="high", status="verified",
                teaser_summary=f"Low Leapfrog Hospital Safety Grade ({g}) — a visible negative safety signal for patients and AI.",
                current_state=f"{name} holds a Leapfrog grade of {g}.",
                expected_state="An improving Leapfrog grade (C or better), with verified survey participation.",
                remediation_type="leapfrog_submission", evidence=[]))
        star = safety.get("cms_star_rating")
        if star is not None and star <= 2:
            findings.append(dict(
                platform="safety", category="opportunity", severity="medium", status="verified",
                teaser_summary=f"Low CMS Overall Star Rating ({star}★) — a quality signal patients and AI assistants weight.",
                current_state=f"{name} has a CMS Overall Hospital Quality Star Rating of {star}.",
                expected_state="Improved CMS measures (3★+); ensure CMS Care Compare data is complete and current.",
                remediation_type="quality_improvement", evidence=[]))
    return findings


# ── Orchestrator ──────────────────────────────────────────────────────────────

def analyze_content(entity_name: str, website_urls: list, city: str = "", state: str = "",
                    entity_kind: str = "hospital", reputation: dict = None,
                    safety: dict = None, on_event=None) -> ContentFindings:
    """Run the verified content checks and return a ContentFindings object.

    Never raises: any component failure yields not_assessed findings and a
    whole-run status that reflects what could/couldn't be checked. `on_event`,
    when provided, streams sub-step progress (used for the longer directory pass)."""
    emit = on_event or (lambda e: None)
    urls = [_norm_url(u) for u in (website_urls or []) if (u or "").strip()][:_MAX_SITES]
    raw: list = []
    snapshot = {"website_urls": urls, "wikipedia_article": None,
                "wikidata_qid": None, "pages_crawled": 0}
    partial = False

    browser = _BrowserFetcher()   # lazy: Chromium launches only if a site is blocked
    with _client() as client:
        try:
            # Website(s)
            snaps = []
            budget = _MAX_PAGES_TOTAL
            for u in urls:
                per = min(_MAX_PAGES_PER_SITE, max(1, budget))
                s = _crawl_site(client, u, per, browser)
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
                wd, qid = _check_wikidata(client, entity_name, urls[0] if urls else "", entity_kind)
                raw += wd
                snapshot["wikidata_qid"] = qid
            except Exception:
                partial = True
            # Wikipedia
            try:
                wp, title = _check_wikipedia(client, entity_name, entity_kind)
                raw += wp
                snapshot["wikipedia_article"] = title
            except Exception:
                partial = True
            # Healthcare directories: NOT wired at the org level. Feasibility
            # probing found these directories are physician-indexed with fuzzy
            # fallback — a practice/org search returns loosely-related doctors, so
            # org-level "presence" can't be reliably determined (real orgs like
            # Novant Health read as "not found"). Step 2 pivoted to service-line
            # listing/reputation analysis; see docs/service-line-listing-analysis.md.
            # on_event is kept as plumbing for that (longer) pass.
        finally:
            browser.close()

    # Reputation (location basis) — from the base diagnostic's verified data; no
    # network calls here, so it runs outside the HTTP client block.
    try:
        raw += _check_reputation(reputation)
    except Exception:
        partial = True
    # Safety / quality (hospital Leapfrog + CMS) — same verified base data.
    try:
        raw += _check_safety(safety)
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
