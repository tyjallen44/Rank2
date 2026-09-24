"""Leapfrog Hospital Safety Grade lookup (hospitalsafetygrade.org).

The public site renders results client-side, and its search only returns hospitals for a
CITY + STATE (a bare name search returns nothing). So: load the city/state results in a
headless browser, read every result card (name, address, grade class), and fuzzy-match the
requested hospital. Distinguishes three outcomes:

  graded      → {"grade": "A".."F"}
  not_graded  → the hospital is listed but Leapfrog assigns no grade this cycle (class grade-gna)
  not_found   → no card in that city/state matched the name

Grades are published twice a year; the card's date label is returned as `cycle`.
"""
from __future__ import annotations

import re
from typing import Optional

_SEARCH = "https://www.hospitalsafetygrade.org/search?findBy=hospital&zip_code=&city={city}&state_prov={state}&hospital="
_UA = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/128 Safari/537.36"   # this exact string passes the site's bot check; longer UAs get a challenge page
_STOP = frozenset({"hospital", "medical", "center", "centre", "health", "healthcare", "system", "regional", "memorial",
                   "general", "community", "care", "of", "the", "at", "and", "inc", "llc", "dba", "usa"})
_SYSTEM_PREFIXES = ("usa health ", "uab ", "hca ", "ascension ", "baptist health ", "adventhealth ", "atrium health ",
                    "novant health ", "prisma health ", "ochsner ", "ssm health ", "mercy ", "st. luke's ", "trinity health ")


def _tokens(name: str) -> frozenset:
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return frozenset(t for t in n.split() if t not in _STOP and len(t) > 1)


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _variants(name: str) -> list:
    n = (name or "").strip()
    out = [n]
    low = n.lower()
    for p in _SYSTEM_PREFIXES:
        if low.startswith(p):
            out.append(n[len(p):].strip())
    m = re.match(r"^(.*?)\s*[-–—]\s*(.+)$", n)          # "System - Campus" → "Campus"
    if m:
        out.append(m.group(2).strip())
    return [v for v in out if v]


def search_city(city: str, state: str, timeout_ms: int = 45000) -> list:
    """All result cards for a city/state: [{name, address, grade|None, status, href, cycle}]."""
    from playwright.sync_api import sync_playwright
    url = _SEARCH.format(city=(city or "").strip().replace(" ", "+"), state=(state or "").strip().upper())
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=_UA)
            resp = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            body = page.inner_text("body")[:600].lower()
            if (resp is not None and resp.status == 202) or "confirm you are human" in body or "security check" in body:
                raise RuntimeError("bot challenge page")
            cards = page.evaluate("""() => Array.from(document.querySelectorAll('.gradeWrapper')).map(g => {
                const wrap = g.parentElement || g;
                const nameEl = wrap.querySelector('.detailWrapper .name a, .name a, .name');
                const addr = wrap.querySelector('.detailWrapper .address, .address');
                const cls = Array.from(g.classList).find(c => /^grade-/.test(c)) || '';
                const date = g.querySelector('.date');
                return { name: nameEl ? nameEl.innerText.trim() : '', href: nameEl && nameEl.getAttribute ? (nameEl.getAttribute('href') || '') : '',
                         address: addr ? addr.innerText.replace(/\\s+/g, ' ').trim() : '', cls, cycle: date ? date.innerText.trim() : '' };
            })""")
        finally:
            browser.close()
    out = []
    for c in cards or []:
        letter = c.get("cls", "").replace("grade-", "").strip().lower()
        if letter in ("a", "b", "c", "d", "f"):
            grade, status = letter.upper(), "graded"
        else:
            grade, status = None, "not_graded"
        out.append({"name": c.get("name") or "", "address": c.get("address") or "", "grade": grade, "status": status,
                    "href": ("https://www.hospitalsafetygrade.org" + c["href"]) if c.get("href", "").startswith("/") else c.get("href", ""),
                    "cycle": c.get("cycle") or ""})
    return out


_CACHE_DAYS = 30     # grades change twice a year; one browser fetch per city per month keeps us under the site's bot threshold


def _cached_city(city: str, state: str):
    """(cards, fetched_at) from the DB cache, or None. Fail-soft."""
    try:
        import json
        from datetime import datetime, timedelta
        from ..db import get_connection
        con = get_connection()
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS leapfrog_city_cache (
                               city_key VARCHAR PRIMARY KEY, cards VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL)""")
            row = con.execute("SELECT cards, fetched_at FROM leapfrog_city_cache WHERE city_key = ?",
                              [f"{(city or '').strip().lower()}|{(state or '').strip().upper()}"]).fetchone()
        finally:
            con.close()
        if row and row[1] and datetime.utcnow() - row[1] < timedelta(days=_CACHE_DAYS):
            return json.loads(row[0]), row[1]
    except Exception:
        pass
    return None


def _store_city(city: str, state: str, cards: list) -> None:
    try:
        import json
        from datetime import datetime
        from ..db import get_connection
        con = get_connection()
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS leapfrog_city_cache (
                               city_key VARCHAR PRIMARY KEY, cards VARCHAR NOT NULL, fetched_at TIMESTAMP NOT NULL)""")
            key = f"{(city or '').strip().lower()}|{(state or '').strip().upper()}"
            con.execute("DELETE FROM leapfrog_city_cache WHERE city_key = ?", [key])
            con.execute("INSERT INTO leapfrog_city_cache (city_key, cards, fetched_at) VALUES (?, ?, ?)",
                        [key, json.dumps(cards), datetime.utcnow()])
        finally:
            con.close()
    except Exception:
        pass


def fetch_leapfrog(name: str, city: str, state: str) -> dict:
    """{'status': graded|not_graded|not_found|error, 'grade': letter|None, 'matched_name', 'url', 'cycle', 'note'}.
    City results are cached for a month, so most lookups never touch the site."""
    cached = _cached_city(city, state)
    if cached:
        cards = cached[0]
    else:
        try:
            cards = search_city(city, state)
        except Exception as exc:
            return {"status": "error", "grade": None, "matched_name": None, "url": None, "cycle": None,
                    "note": f"Leapfrog lookup unavailable ({'bot challenge' if 'challenge' in str(exc) else type(exc).__name__}) — grade not verified this run"}
        if cards:
            _store_city(city, state, cards)
    if not cards:
        return {"status": "not_found", "grade": None, "matched_name": None, "url": None, "cycle": None,
                "note": f"Leapfrog lists no hospitals for {city}, {state}"}
    best, best_sim = None, 0.0
    for v in _variants(name):
        for c in cards:
            s = _similarity(v, c["name"])
            if s > best_sim or (s == best_sim and best and c["name"].lower() == v.lower()):
                best, best_sim = c, s
    if not best or best_sim < 0.6:
        return {"status": "not_found", "grade": None, "matched_name": None, "url": None, "cycle": None,
                "note": f"Leapfrog: no hospital in {city}, {state} matched '{name}' (closest: {best['name'] if best else 'none'})"}
    return {"status": best["status"], "grade": best["grade"], "matched_name": best["name"], "url": best["href"], "cycle": best["cycle"],
            "note": (f"Leapfrog Hospital Safety Grade {best['grade']} ({best['cycle']}) — listed as '{best['name']}'" if best["grade"]
                     else f"Leapfrog lists '{best['name']}' but assigns no grade this cycle ({best['cycle']})")}


def fetch_leapfrog_grade(name: str, city: str, state: str) -> Optional[str]:
    """Compatibility wrapper: the letter grade, or None (not graded / not found / error)."""
    return fetch_leapfrog(name, city, state).get("grade")
