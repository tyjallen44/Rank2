"""CMS Care Compare — the authoritative hospital census + overall star rating.

Uses the public CMS Provider Data datastore query API (no key required) against
the *Hospital General Information* dataset (id ``xubh-q36u``). This is the
denominator for a hospital-market run: every Medicare-certified hospital in the
state, with its type, ownership, ED status, county, and CMS overall star rating
— so the report ranks the top of a *complete* field rather than a curated guess,
and the Outcomes & Safety tier is anchored to a real CMS rating.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

_DATASET = "xubh-q36u"  # CMS Care Compare — Hospital General Information
_BASE = f"https://data.cms.gov/provider-data/api/1/datastore/query/{_DATASET}/0"
_PAGE = 200


@dataclass
class CmsHospital:
    facility_id: str
    name: str
    city: str
    county: str
    state: str
    hospital_type: str
    ownership: str
    emergency_services: bool
    overall_rating: Optional[int]  # CMS overall star rating 1–5, or None if not rated

    @property
    def is_material(self) -> bool:
        """Material = an acute-care hospital with an ED (the rankable tier).

        Critical-access, psychiatric-only, and specialty facilities are real but
        belong below the fold; callers tier on this flag rather than dropping
        them silently.
        """
        return self.hospital_type == "Acute Care Hospitals" and self.emergency_services


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _to_rating(raw: str | None) -> Optional[int]:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def list_hospitals(
    state: str,
    cities: list[str] | None = None,
    counties: list[str] | None = None,
    *,
    timeout: float = 60.0,
) -> list[CmsHospital]:
    """Return the CMS hospital census for a state, optionally narrowed to a metro.

    ``cities`` / ``counties`` are matched case-insensitively client-side (the
    metro usually spans several counties). Pass neither to get the whole state.
    """
    state = state.strip().upper()
    city_set = {c.strip().lower() for c in (cities or [])}
    county_set = {c.strip().lower() for c in (counties or [])}

    params_base = {
        "conditions[0][property]": "state",
        "conditions[0][value]": state,
        "conditions[0][operator]": "=",
        "limit": _PAGE,
    }

    hospitals: list[CmsHospital] = []
    offset = 0
    seen: set[str] = set()
    while True:
        params = dict(params_base, offset=offset)
        try:
            resp = httpx.get(_BASE, params=params, timeout=timeout)
            resp.raise_for_status()
            rows = resp.json().get("results", [])
        except (httpx.HTTPError, ValueError):
            break
        if not rows:
            break
        for row in rows:
            fid = _norm(row.get("facility_id"))
            if not fid or fid in seen:
                continue
            seen.add(fid)
            city = _norm(row.get("citytown"))
            county = _norm(row.get("countyparish"))
            if city_set and city.lower() not in city_set:
                continue
            if county_set and county.lower() not in county_set:
                continue
            hospitals.append(
                CmsHospital(
                    facility_id=fid,
                    name=_norm(row.get("facility_name")).title(),
                    city=city.title(),
                    county=county.title(),
                    state=state,
                    hospital_type=_norm(row.get("hospital_type")),
                    ownership=_norm(row.get("hospital_ownership")),
                    emergency_services=_norm(row.get("emergency_services")).lower() == "yes",
                    overall_rating=_to_rating(row.get("hospital_overall_rating")),
                )
            )
        if len(rows) < _PAGE:
            break
        offset += _PAGE

    hospitals.sort(key=lambda h: (h.overall_rating or 0), reverse=True)
    return hospitals


# ── Token helpers for fuzzy name matching ────────────────────────────────────

_STOP = frozenset({
    "hospital", "medical", "center", "health", "system", "healthcare",
    "regional", "memorial", "general", "community", "care", "services",
    "of", "the", "at", "and", "for", "a", "an",
})


def _name_tokens(name: str) -> frozenset[str]:
    import re
    toks = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    return frozenset(t for t in toks if t not in _STOP and len(t) > 1)


def _best_match(name: str, city: str, candidates: list[CmsHospital]) -> Optional[CmsHospital]:
    """Return the CMS record that best matches name+city, or None if no good match."""
    tok_q = _name_tokens(name)
    city_l = city.lower().strip()
    best: Optional[CmsHospital] = None
    best_score = 0.0
    for h in candidates:
        tok_h = _name_tokens(h.name)
        if not tok_q or not tok_h:
            continue
        overlap = len(tok_q & tok_h) / min(len(tok_q), len(tok_h))
        city_bonus = 0.2 if h.city.lower() == city_l else 0.0
        score = overlap + city_bonus
        if score > best_score and overlap >= 0.45:
            best_score = score
            best = h
    return best


def fetch_star_ratings_for_network(
    facilities: list[dict],
    *,
    timeout: float = 60.0,
) -> dict[str, Optional[int]]:
    """Batch-fetch CMS overall star ratings for a network facility roster.

    Groups facilities by state, fetches the full CMS hospital list for each state
    once (no per-hospital API calls), then fuzzy-matches each facility by name+city.

    Returns a dict keyed by lowercase facility name → star rating (1–5) or None.
    """
    from collections import defaultdict

    by_state: dict[str, list[dict]] = defaultdict(list)
    for fac in facilities:
        st = (fac.get("state") or "").strip().upper()
        if st:
            by_state[st].append(fac)

    result: dict[str, Optional[int]] = {}
    for state, facs in by_state.items():
        try:
            cms_list = list_hospitals(state, timeout=timeout)
        except Exception:
            cms_list = []
        for fac in facs:
            key = fac.get("name", "").lower()
            matched = _best_match(fac.get("name", ""), fac.get("city", ""), cms_list)
            result[key] = matched.overall_rating if matched else None

    return result
