"""Website facts verified by crawl (heart-surgery item 3, increment 1).

For practices: the Identity & Machine-Readability pillar's "website machine-readability"
sub-score (20 of 100 points in the practice rubric) is MEASURED here instead of estimated
by the model. For hospitals and practices alike: quality claims the site makes (Leapfrog
grade, CMS stars, U.S. News, Magnet, Joint Commission) are recorded so the report can say
whether the organization surfaces its record where assistants read, and flag a claim that
disagrees with the verified value.

Band (approved 2026-09-24): org schema 6 · Physician schema 4 · crawlable physician pages 5 ·
sitemap 2 · llms.txt 2 · robots allows AI crawlers 1 = 20. A bot wall scores 0 (verified: AI
crawlers are turned away the same way). An unreachable site is NOT measured — the model's read
stands and Score Evidence drops a level.
"""
from __future__ import annotations

import re
from typing import Optional

ORG_SCHEMA = {"MedicalOrganization", "MedicalClinic", "MedicalBusiness", "LocalBusiness", "Hospital", "Physician"}
BAND = {"org_schema": 6, "physician_schema": 4, "physician_pages": 5, "sitemap": 2, "llms_txt": 2, "robots_allows_ai": 1}
MODEL_ASSUMED_DEFAULT = 10      # used when the model did not state the sub-score it assumed

# The readers that build AI answers, asked for the homepage by name. A site that challenges these
# is closed to AI assistants regardless of what its robots.txt says.
AI_CRAWLERS = [
    ("GPTBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)"),
    ("ChatGPT-User", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot"),
    ("ClaudeBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"),
    ("PerplexityBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"),
    ("Google-Extended", "Mozilla/5.0 (compatible; Google-Extended)"),
]
_BLOCK_CODES = {401, 403, 405, 406, 429, 503}


def probe_ai_crawlers(url: str, timeout: float = 15.0) -> dict:
    """Request the homepage as each AI crawler and as a plain browser. Returns
    {"results": [{"name", "status", "outcome"}], "blocked": [names], "allowed": [names],
     "browser_blocked": bool, "where": "firewall" | "robots" | "open"}.
    outcome: allowed | blocked | error. Fail-soft: never raises."""
    import httpx
    out = {"results": [], "blocked": [], "allowed": [], "browser_blocked": False, "where": "open"}
    if not (url or "").strip():
        return out
    u = url if "://" in url else "https://" + url
    agents = AI_CRAWLERS + [("Browser", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36")]
    for name, ua in agents:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": ua, "Accept": "text/html,*/*"}) as c:
                r = c.get(u)
            body = (r.text or "")[:4000].lower()
            challenged = r.status_code in _BLOCK_CODES or any(k in body for k in ("verify you are human", "just a moment", "attention required", "cf-browser-verification", "access denied"))
            outcome = "blocked" if challenged else ("allowed" if r.status_code < 400 else "error")
            out["results"].append({"name": name, "status": r.status_code, "outcome": outcome})
        except Exception as exc:
            out["results"].append({"name": name, "status": None, "outcome": "error", "note": type(exc).__name__})
            outcome = "error"
        if name == "Browser":
            out["browser_blocked"] = outcome == "blocked"
        elif name == "Google-Extended":
            # Google-Extended is a robots.txt product token, not a crawler user agent (Google's
            # own docs: it has no separate request UA). A 403 on that string is generic
            # non-browser filtering, so it is recorded but never decides "blocked".
            continue
        elif outcome == "blocked":
            out["blocked"].append(name)
        elif outcome == "allowed":
            out["allowed"].append(name)
    if out["blocked"]:
        out["where"] = "firewall"
    return out


def fetch_website_facts(url: Optional[str], *, page_budget: int = 8) -> dict:
    """Crawl the site (reusing the content analyzer's crawler) and return the facts.
    status: measured | blocked | unreachable | skipped."""
    if not (url or "").strip():
        return {"status": "skipped", "url": None, "note": "no website on the Google listing"}
    from ..content_analyzer import _client, _BrowserFetcher, _crawl_site, _norm_url
    browser = _BrowserFetcher()
    try:
        with _client() as client:
            snap = _crawl_site(client, _norm_url(url), page_budget, browser)
    except Exception as exc:
        return {"status": "unreachable", "url": url, "note": f"crawl failed: {type(exc).__name__}"}
    finally:
        try:
            browser.close()
        except Exception:
            pass
    if not snap.get("reachable"):
        if snap.get("fetch_status") == "blocked":
            f = {"status": "blocked", "url": url, "pages": 0, "points": 0, "breakdown": {},
                 "note": "site returns a bot wall to non-browser clients — AI crawlers are turned away the same way"}
            f["crawler_probe"] = probe_ai_crawlers(url)
            return f
        return {"status": "unreachable", "url": url, "note": "site could not be reached (DNS / timeout / connection)"}
    types = set(snap.get("schema_types") or set())
    kp = snap.get("key_pages") or []
    provider_pages = [p for p in kp if p.get("provider_page") and p.get("text_len", 0) >= 400]
    facts = {
        "status": "measured", "url": url, "pages": snap.get("pages", 0),
        "org_schema": bool(types & (ORG_SCHEMA - {"Physician"})),
        "physician_schema": ("Physician" in types) or any(p.get("physician_schema") for p in kp),
        "physician_pages": len(provider_pages),
        "sitemap": bool(snap.get("sitemap")),
        "llms_txt": bool(snap.get("llms_txt")),
        "robots_allows_ai": not (snap.get("robots_blocks_ai") or snap.get("robots_blocks_all")),
        "schema_types": sorted(types)[:12],
        "claims": scan_claims(snap.get("text_sample") or ""),
        "site_pages": [{"url": p.get("url"), "text": p.get("text") or ""} for p in kp if p.get("text")] + [{"url": url, "text": snap.get("text_sample") or ""}],
    }
    facts["breakdown"] = {
        "org_schema": BAND["org_schema"] if facts["org_schema"] else 0,
        "physician_schema": BAND["physician_schema"] if facts["physician_schema"] else 0,
        "physician_pages": BAND["physician_pages"] if facts["physician_pages"] else 0,
        "sitemap": BAND["sitemap"] if facts["sitemap"] else 0,
        "llms_txt": BAND["llms_txt"] if facts["llms_txt"] else 0,
        "robots_allows_ai": BAND["robots_allows_ai"] if facts["robots_allows_ai"] else 0,
    }
    facts["points"] = sum(facts["breakdown"].values())
    # Physician bio pages: the hint crawl rarely reaches them; follow the provider directory one hop.
    try:
        bios = _fetch_bio_pages(url, [p.get("url") for p in kp if p.get("provider_page")])
        if bios:
            facts["site_pages"] = facts["site_pages"] + bios
            facts["bio_pages"] = len(bios)
            if not facts["physician_pages"]:
                facts["physician_pages"] = len(bios)
                facts["breakdown"]["physician_pages"] = BAND["physician_pages"]
                facts["points"] = sum(facts["breakdown"].values())
    except Exception:
        pass
    # Even a readable site may challenge the named AI crawlers at the firewall; ask as each of them.
    facts["crawler_probe"] = probe_ai_crawlers(url)
    if facts["crawler_probe"]["blocked"] and not facts["crawler_probe"]["allowed"]:
        facts["status"] = "blocked"
        facts["note"] = "the site challenges every AI crawler by name (firewall / bot-management rule)"
        facts["points"] = 0
        facts["breakdown"] = {k: 0 for k in facts["breakdown"]}
    return facts


_DIRECTORY_PATHS = ("/doctors", "/providers", "/physicians", "/our-providers", "/our-doctors", "/our-team", "/team",
                    "/find-a-doctor", "/find-a-provider", "/meet-our-team", "/meet-the-team", "/staff", "/surgeons")
_CRED_TOKEN = re.compile(r"(^|[-/_])(md|do|dpm|pa|pa-c|np|dds|dmd|phd|dr)($|[-/_.])", re.I)
_BIO_PATH = re.compile(r"(/dr-|/team/|/staff/|/physician[s]?/|/provider[s]?/|/doctor[s]?/|/bio[s]?/|/people/)", re.I)


def _last_token(path: str) -> str:
    return (path.rstrip("/").rsplit("/", 1)[-1] or "").lower()


def _fetch_bio_pages(site_url: str, directory_urls: list, *, max_bios: int = 12, physician_names: list | None = None) -> list:
    """Find the provider directory (known key pages first, then common paths; headless browser when
    the listing is script-rendered) and read physician bio pages linked from it. Links are ranked:
    (1) path or link text contains a known physician's last name, (2) path carries a credential
    token (-md, -do, -dpm, -pa…) or a bio-style path, (3) nothing else. Returns [{url, text}]."""
    from urllib.parse import urljoin, urlparse
    from bs4 import BeautifulSoup
    from ..content_analyzer import _client, _origin, _norm_url, _BrowserFetcher
    origin = _origin(site_url)
    lasts = sorted({(n or "").strip().split()[-1].lower() for n in (physician_names or []) if (n or "").strip()}, key=len, reverse=True)
    out, seen = [], set()
    with _client() as client:
        candidates = [u for u in (directory_urls or []) if u] + [origin + p for p in _DIRECTORY_PATHS]
        dir_html, dir_url = None, None
        for u in candidates:
            try:
                r = client.get(u)
                if r.status_code == 200 and "text/html" in r.headers.get("content-type", "") and len(r.text) > 2000:
                    dir_html, dir_url = r.text, str(r.url); seen.add(_norm_url(u)); seen.add(_norm_url(dir_url)); break
            except Exception:
                continue
        if not dir_html:
            return out

        def _rank_links(html):
            soup = BeautifulSoup(html, "html.parser")
            ranked = []
            for a in soup.find_all("a", href=True):
                href = urljoin(origin, a["href"])
                if urlparse(href).netloc != urlparse(origin).netloc or _norm_url(href) in seen:
                    continue
                path = urlparse(href).path
                if not path or path.rstrip("/").endswith(tuple(_DIRECTORY_PATHS)):
                    continue
                tok, text = _last_token(path), (a.get_text(" ", strip=True) or "").lower()
                score = 0
                if lasts and any(re.search(r"(^|[-_/ ])" + re.escape(l) + r"($|[-_/ .,])", tok) or (l in text and len(text) < 60) for l in lasts):
                    score = 3
                elif _CRED_TOKEN.search(tok) or _BIO_PATH.search(path):
                    score = 2
                if score:
                    seen.add(_norm_url(href)); ranked.append((score, href))
            ranked.sort(key=lambda x: -x[0])
            return [h for _, h in ranked]

        links = _rank_links(dir_html)
        if not links:                                   # script-rendered listing → render it
            b = _BrowserFetcher()
            try:
                html, _st = b.fetch_html(dir_url or candidates[0])
                if html:
                    links = _rank_links(html)
            except Exception:
                pass
            finally:
                try:
                    b.close()
                except Exception:
                    pass
        for href in links[:max_bios]:
            try:
                r = client.get(href)
                if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                    t = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
                    if len(t) > 300:
                        out.append({"url": href, "text": t[:20000]})
            except Exception:
                continue
    return out


_GRADE = r"(?:grade|graded|rated)\s*(?:of\s*)?[\"'\u201c\u2018]?([A-F])[\"'\u201d\u2019]?\b|\b(?:an?\s+)?[\"'\u201c\u2018]?([A-F])[\"'\u201d\u2019]?\s+(?:hospital\s+)?safety\s+grade"


def scan_claims(text: str) -> dict:
    """Quality claims the site makes, from crawled page text. Conservative: a Leapfrog grade
    is only reported when a letter appears within ~160 characters of the word 'Leapfrog'."""
    t = re.sub(r"\s+", " ", text or "")
    low = t.lower()
    claims = {"leapfrog": None, "leapfrog_mentioned": "leapfrog" in low, "cms_stars": None,
              "usnews": bool(re.search(r"u\.?s\.? news", low)), "magnet": "magnet" in low and "designat" in low or "magnet recogni" in low,
              "joint_commission": ("joint commission" in low) or ("gold seal" in low)}
    for m in re.finditer(r"leapfrog", low):
        win = t[max(0, m.start() - 160): m.end() + 160]
        g = re.search(_GRADE, win, re.I)
        if g:
            letter = (g.group(1) or g.group(2) or "").upper()
            if letter in "ABCDF" and letter:
                claims["leapfrog"] = letter
                break
    m = re.search(r"(?:cms|medicare|care compare)[^.]{0,80}?\b([1-5])[- ]?star", low) or re.search(r"\b([1-5])[- ]?star[^.]{0,60}?(?:cms|medicare|care compare)", low)
    if m:
        claims["cms_stars"] = int(m.group(1))
    return claims


def apply_identity_override(tier: Optional[int], model_pts: Optional[int], measured_pts: int) -> Optional[int]:
    """Replace the model's assumed website sub-score with the measured one inside the pillar total."""
    if tier is None:
        return None
    mp = MODEL_ASSUMED_DEFAULT if model_pts is None else max(0, min(20, int(model_pts)))
    return max(0, min(100, int(round(tier - mp + max(0, min(20, measured_pts))))))


def evidence_lines(f: dict, kind: str = "practice") -> str:
    if not f or f.get("status") == "skipped":
        return ""
    lines = ["", "=== Website facts (verified by crawl of the listed website — USE VERBATIM) ==="]
    if f.get("status") == "unreachable":
        lines.append(f"Website {f.get('url')}: could not be reached ({f.get('note')}). Treat machine-readability as UNVERIFIED; do not assume.")
        return "\n".join(lines) + "\n"
    if f.get("status") == "blocked":
        pr = f.get("crawler_probe") or {}
        who = ", ".join(pr.get("blocked") or []) or "automated readers"
        lines.append(f"Website {f.get('url')}: turns away AI crawlers at the firewall (verified by requesting the homepage as {who}"
                     f"{'; a normal browser request was challenged too' if pr.get('browser_blocked') else ''}). robots.txt is not the cause. "
                     "Website machine-readability sub-score = 0/20. Say plainly that AI assistants cannot read the site.")
        return "\n".join(lines) + "\n"
    pr = f.get("crawler_probe") or {}
    if pr.get("blocked"):
        lines.append(f"Firewall check: the site challenges {', '.join(pr['blocked'])} by name but admits {', '.join(pr.get('allowed') or []) or 'no other AI crawler'}; "
                     "mention which assistants are shut out.")
    yn = lambda b: "yes" if b else "no"
    lines.append(f"Website {f.get('url')} ({f.get('pages')} pages read): organization schema {yn(f.get('org_schema'))}; "
                 f"Physician schema {yn(f.get('physician_schema'))}; crawlable physician/provider pages {f.get('physician_pages')}; "
                 f"sitemap {yn(f.get('sitemap'))}; llms.txt {yn(f.get('llms_txt'))}; AI crawlers allowed by robots.txt {yn(f.get('robots_allows_ai'))}.")
    if kind in ("practice", "service_line"):
        lines.append(f"Website machine-readability sub-score (measured) = {f.get('points')}/20. The system applies this value; "
                     "state the website_readability_pts you would otherwise have assumed so it can be replaced.")
    c = f.get("claims") or {}
    cl = []
    if c.get("leapfrog"):
        cl.append(f"states a Leapfrog Safety Grade of {c['leapfrog']}")
    elif c.get("leapfrog_mentioned"):
        cl.append("mentions Leapfrog without a legible grade")
    if c.get("cms_stars"):
        cl.append(f"states a CMS {c['cms_stars']}-star rating")
    if c.get("usnews"):
        cl.append("references U.S. News")
    if c.get("magnet"):
        cl.append("references Magnet designation")
    if c.get("joint_commission"):
        cl.append("references Joint Commission accreditation")
    lines.append("Quality claims on the site: " + ("; ".join(cl) if cl else "none found in the pages read") + ".")
    return "\n".join(lines) + "\n"


def summary(f: dict, kind: str = "practice") -> Optional[str]:
    """Short Score Evidence phrase."""
    if not f or f.get("status") == "skipped":
        return None
    if f.get("status") == "unreachable":
        return "website unreachable — machine-readability unverified"
    if f.get("status") == "blocked":
        return "website blocks AI crawlers (verified)"
    if kind in ("practice", "service_line"):
        return f"website facts verified by crawl ({f.get('points')}/20)"
    return "website quality claims checked by crawl"


def ai_access_problem(f: Optional[dict]) -> Optional[dict]:
    """The executive-facing alert when the site cannot be read by AI assistants.
    Returns {"level": "critical"|"warning", "title", "body", "first_move"} or None."""
    f = f or {}
    if f.get("status") == "blocked":
        pr = f.get("crawler_probe") or {}
        blocked = pr.get("blocked") or []
        evidence = (f" We asked for your homepage as {', '.join(blocked)} and each was turned away"
                    f"{' — a normal browser too' if pr.get('browser_blocked') else ''}.") if blocked else ""
        where = (" Your robots.txt is not the cause; the block is a firewall or bot-management rule (for example a CDN's "
                 "\"block AI bots\" setting), so that is where your web team should look.")
        return {
            "level": "critical",
            "title": "URGENT: AI assistants cannot read your website",
            "body": ("Your website turns away the crawlers behind ChatGPT, Claude, Gemini and Perplexity." + evidence +
                     " Nothing you publish — services, physicians, credentials, hours, insurance, awards — reaches the answers "
                     "patients are getting. AI assistants describe you from other people's pages, and your competitors' content "
                     "fills the gap. A person opening your site in a browser, or an assistant fetching one page on that person's "
                     "behalf, may still get through; the automated readers that build everyday answers do not." + where +
                     " This is a configuration change, not a rebuild, and it should be fixed before anything else in this report. "
                     "Get this page to whoever runs your website today."),
            "first_move": ("Allow the AI crawlers through your website's firewall or bot-management rules (GPTBot, ChatGPT-User, "
                           "ClaudeBot, PerplexityBot, Google-Extended) on public pages, keeping portals and patient data protected — "
                           "this is not a robots.txt change. Until this is done, AI assistants cannot read anything you publish, so "
                           "every other website fix below has no effect."),
        }
    if f.get("status") == "measured" and f.get("robots_allows_ai") is False:
        return {
            "level": "warning",
            "title": "Your website tells AI assistants to stay out",
            "body": ("Your robots.txt file instructs AI crawlers (such as GPTBot and ClaudeBot) not to read your pages, so what you "
                     "publish is left out of the answers patients are getting and assistants describe you from other people's "
                     "pages. This is a one-line configuration change. Get this page to whoever runs your website."),
            "first_move": ("Remove the robots.txt rules that disallow AI crawlers (GPTBot, ClaudeBot, PerplexityBot, "
                           "Google-Extended) on public pages so assistants can read what you publish."),
        }
    return None


def claim_mismatch(f: dict, quality: Optional[dict]) -> Optional[str]:
    """A sentence when the site's Leapfrog / CMS claim disagrees with the verified value."""
    c = (f or {}).get("claims") or {}
    q = quality or {}
    out = []
    if c.get("leapfrog") and q.get("leapfrog_grade") and c["leapfrog"] != q["leapfrog_grade"]:
        out.append(f"the website states Leapfrog grade {c['leapfrog']} but hospitalsafetygrade.org shows {q['leapfrog_grade']} ({q.get('leapfrog_cycle') or 'current cycle'})")
    if c.get("cms_stars") and q.get("cms_star") and c["cms_stars"] != q["cms_star"]:
        out.append(f"the website states a CMS {c['cms_stars']}-star rating but Care Compare shows {q['cms_star']}")
    return "; ".join(out) or None
