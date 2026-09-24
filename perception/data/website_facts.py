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
    return facts


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
        lines.append(f"Website {f.get('url')}: serves a bot wall to non-browser clients. AI crawlers are turned away the same way "
                     "(verified). Website machine-readability sub-score = 0/20.")
        return "\n".join(lines) + "\n"
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
        return {
            "level": "critical",
            "title": "URGENT: AI assistants cannot read your website",
            "body": ("Your website turns away automated readers, and that includes the crawlers behind ChatGPT, Claude, "
                     "Gemini and Perplexity. Nothing you publish — services, physicians, credentials, hours, insurance, awards — "
                     "reaches the answers patients are getting. AI assistants describe you from other people's pages, and your "
                     "competitors' content fills the gap. This is usually a bot-protection setting on your website, not a rebuild, "
                     "and it should be fixed before anything else in this report. Get this page to whoever runs your website today."),
            "first_move": ("Allow AI crawlers through your website's bot protection (GPTBot, ClaudeBot, PerplexityBot, "
                           "Google-Extended) on public pages, keeping portals and patient data protected. Until this is done, "
                           "AI assistants cannot read anything you publish, so every other website fix below has no effect."),
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
