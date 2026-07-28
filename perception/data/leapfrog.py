"""Leapfrog Hospital Safety Grade scraper.

Fetches the current A/B/C/D/F Hospital Safety Grade from leapfroggroup.org
for a named hospital using Playwright.  Grades are published semi-annually;
this always returns the current published grade.

Usage:
    grade = fetch_leapfrog_grade("Atrium Health Pineville", "Charlotte", "NC")
    # → "A" | "B" | "C" | "D" | "F" | None
"""
from __future__ import annotations

import re
from typing import Optional


_SEARCH_URL = "https://www.leapfroggroup.org/ratings-report"
_GRADE_RE   = re.compile(r'\b([A-F])\b')
_VALID      = frozenset("ABCDF")
_STOP       = frozenset({
    "hospital", "medical", "center", "health", "system", "regional",
    "memorial", "general", "community", "care", "of", "the", "at",
})


def _tokens(name: str) -> frozenset[str]:
    toks = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    return frozenset(t for t in toks if t not in _STOP and len(t) > 1)


def _name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def fetch_leapfrog_grade(name: str, city: str, state: str) -> Optional[str]:
    """Return the Leapfrog Hospital Safety Grade for a hospital, or None.

    Launches a headless Chromium browser, searches leapfroggroup.org, and
    extracts the grade letter.  Returns None if the hospital is not rated,
    not found, or the scrape fails for any reason.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page    = browser.new_page()

            # Load the ratings search page
            page.goto(_SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)

            # Try to fill the hospital name search field
            # Leapfrog uses various input patterns — try each in priority order
            filled = False
            for selector in [
                'input[placeholder*="ospital"]',
                'input[placeholder*="earch"]',
                'input[name*="ospital"]',
                'input[name*="earch"]',
                'input[type="search"]',
                'input[type="text"]:first-of-type',
            ]:
                try:
                    page.fill(selector, name, timeout=2000)
                    filled = True
                    break
                except Exception:
                    continue

            if not filled:
                browser.close()
                return None

            # Try to set state filter if a dropdown exists
            for sel_selector in [
                'select[name*="tate"]',
                'select[id*="tate"]',
                'select[aria-label*="tate"]',
            ]:
                try:
                    page.select_option(sel_selector, state, timeout=1000)
                    break
                except Exception:
                    continue

            # Submit the search
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)

            # Also try clicking a search button if Enter didn't trigger results
            for btn_selector in [
                'button[type="submit"]',
                'button:has-text("Search")',
                'input[type="submit"]',
            ]:
                try:
                    page.click(btn_selector, timeout=1000)
                    page.wait_for_timeout(2000)
                    break
                except Exception:
                    continue

            # Extract page text and look for grade near matching hospital name
            body_text = page.inner_text("body")
            browser.close()

        return _parse_grade(body_text, name, state)

    except Exception:
        return None


def _parse_grade(text: str, hospital_name: str, state: str) -> Optional[str]:
    """Extract the most likely grade for this hospital from page text.

    Strategy: find lines/sections that mention the hospital name (fuzzy),
    then look for a grade letter in the surrounding context.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Find lines that closely match the hospital name
    best_grade: Optional[str] = None
    best_sim   = 0.4  # minimum similarity threshold

    for i, line in enumerate(lines):
        sim = _name_similarity(line, hospital_name)
        if sim > best_sim:
            # Look for a standalone grade letter in the next ~5 lines
            context = " ".join(lines[max(0, i-1): i+6])
            grade = _extract_grade_from_context(context)
            if grade:
                best_sim   = sim
                best_grade = grade

    return best_grade


def _extract_grade_from_context(context: str) -> Optional[str]:
    """Find a valid grade letter (A/B/C/D/F) in a short text snippet.

    Avoids false positives by requiring the letter to appear as a standalone
    word or after common patterns like "Grade: A" or "Safety Grade A".
    """
    # Pattern 1: explicit "Grade" label followed by letter
    explicit = re.search(
        r'(?:grade|safety\s+grade|hospital\s+grade)[:\s]+([ABCDF])\b',
        context, re.IGNORECASE
    )
    if explicit:
        return explicit.group(1).upper()

    # Pattern 2: standalone grade letter surrounded by whitespace/punctuation
    standalone = re.findall(r'(?<!\w)([ABCDF])(?!\w)', context)
    valid = [g for g in standalone if g in _VALID]
    if len(valid) == 1:
        return valid[0]

    return None
