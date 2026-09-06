"""Provider-directory analysis — Phase 2a: org-level presence checks.

AI assistants recommend providers from healthcare directories, not from a
hospital's brand page. This module checks whether an organization/practice has
a presence on the priority directories and returns findings in the same
ContentFinding shape used by content_analyzer, so they render into the shared
Report 1 (keys section) and Report 2 (detailed).

FEASIBILITY FINDING (Phase 2a probing, 2026-09-06): org-level presence on these
directories is NOT reliably detectable. All three are physician-indexed with
fuzzy fallback — a practice/org name search (e.g. "Charlotte Radiology",
"Novant Health") returns loosely-related individual doctors, not the org as a
matchable entity, so real orgs read as "not found". Vitals returns nothing for a
practice-name search; Zocdoc 403s automated clients. Therefore this module is
NOT wired into analyze_content: reliable directory detection is per-provider and
moves to Phase 2c (roster discovery -> per-physician lookup). The helpers below
(browser card parsing, conservative name matching) are the 2c foundation.

Discipline: every check is timeout-bounded and FAIL-SOFT, and — given the above —
this module NEVER asserts org-level absence; it only confirms a strong match or
returns `not_assessed` ("couldn't verify").
"""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

# Healthgrades result cards link to these path prefixes; matching against the
# anchor text of these links (not the whole page) avoids the search box / title
# echoing the query and producing a false "found".
_HG_RESULT_HREF = re.compile(r"/(physician|group-directory|practice|providers?)/", re.I)

# Generic words dropped when building a distinctive-name phrase to match.
_GENERIC = {"the", "of", "and", "for", "a", "an", "inc", "llc", "pa", "pc",
            "group", "associates", "association", "medical", "health",
            "healthcare", "center", "centre", "clinic", "clinics", "care",
            "physicians", "physician", "practice", "practices", "services",
            "system", "systems", "hospital", "hospitals", "institute"}


def _norm(s: str) -> str:
    """Lowercase and collapse every non-alphanumeric run to a single space."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _name_matches_card(entity_name: str, card_text: str) -> bool:
    """True if a single result-card name matches the entity: the full normalized
    name phrase is a substring, OR every distinctive (non-generic, >=4-char)
    token appears in that one card. Conservative — biased against false 'found'."""
    name = _norm(entity_name)
    card = _norm(card_text)
    if not name or not card:
        return False
    if name in card:
        return True
    distinctive = [t for t in name.split() if len(t) >= 4 and t not in _GENERIC]
    return len(distinctive) >= 2 and all(t in card for t in distinctive)


def _healthgrades_result_names(html: str) -> list:
    """Anchor text of Healthgrades result cards (physician/group/practice links).
    These are the actual matches; the page chrome echoes the query and is ignored."""
    names = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if _HG_RESULT_HREF.search(a["href"]):
                t = a.get_text(" ", strip=True)
                if t:
                    names.append(t)
    except Exception:
        pass
    return names


def _finding(status: str, severity: str, teaser: str, current: str,
             expected: str, evidence: list) -> dict:
    return dict(platform="directory", category="opportunity", severity=severity,
                status=status, teaser_summary=teaser, current_state=current,
                expected_state=expected, remediation_type="directory_update",
                evidence=evidence)


def _check_healthgrades(entity_name: str, city: str, state: str,
                        entity_kind: str, browser) -> dict:
    """Org-level Healthgrades presence. Returns one finding dict."""
    where = ", ".join(p for p in [city, state] if p)
    url = ("https://www.healthgrades.com/usearch?what=" + quote(entity_name)
           + "&where=" + quote(where))
    html = browser.fetch_rendered_html(url) if browser is not None else None
    if not html:
        return _finding(
            "not_assessed", "low",
            "Healthgrades presence could not be verified at analysis time.",
            "The Healthgrades search could not be reached or read (blocked/timeout).",
            "A claimed, accurate Healthgrades presence for the practice and its providers.",
            [url])
    card_names = _healthgrades_result_names(html)
    if any(_name_matches_card(entity_name, c) for c in card_names):
        return _finding(
            "partial", "low",
            "A Healthgrades listing was found — confirm it's yours and that it's claimed and accurate.",
            f"A result matching '{entity_name}' appears on Healthgrades for {where or 'this area'}.",
            "A claimed listing with correct name, specialties, locations, and linked provider profiles.",
            [url])
    # No confident org-level match. Because these directories are physician-indexed
    # (see module docstring), absence is NOT reliably determinable at the org level,
    # so we return "couldn't verify" — never a false "missing". Real detection is
    # per-provider (Phase 2c).
    return _finding(
        "not_assessed", "low",
        "Org-level Healthgrades presence could not be reliably determined.",
        f"No Healthgrades org result confidently matched '{entity_name}'; the directory is "
        "physician-indexed, so org-level absence can't be asserted.",
        "Per-provider Healthgrades coverage across the practice's physicians (assessed in the provider audit).",
        [url])


def check_org_directory_presence(entity_name: str, city: str = "", state: str = "",
                                 entity_kind: str = "hospital", browser=None,
                                 on_event=None) -> list:
    """Return org-level directory findings (Phase 2a: Healthgrades only).
    `browser` is a content_analyzer._BrowserFetcher (reused headless Chromium).
    `on_event` streams progress; never raises."""
    emit = on_event or (lambda e: None)
    findings: list = []

    emit({"type": "text", "text": "\nChecking healthcare directories — Healthgrades…"})
    try:
        f = _check_healthgrades(entity_name, city, state, entity_kind, browser)
        findings.append(f)
        _status = {"partial": "found", "verified": "not found",
                   "not_assessed": "couldn't verify"}.get(f["status"], f["status"])
        emit({"type": "text", "text": f"  Healthgrades: {_status}"})
    except Exception:
        emit({"type": "text", "text": "  Healthgrades: couldn't verify"})

    # Vitals & Zocdoc are physician-indexed (and Zocdoc blocks automated clients),
    # so they are checked per-provider in Phase 2c, not at the org level here.
    return findings
