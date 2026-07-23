"""HRSA Health Center Program public data lookup.

Fetches grantee/look-alike status, site counts, service lines, and languages
from the HRSA Find a Health Center public API. Falls back gracefully on error —
the FQHC analyzer runs without HRSA data when the API is unavailable.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


_HRSA_API_BASE = "https://findahealthcenter.hrsa.gov/api/HealthCenter"
_TIMEOUT = 8


def _get(url: str) -> list | dict | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Rank2-Pulse/1.0 (health center research tool)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError):
        return None


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _name_overlap(a: str, b: str) -> float:
    """Jaccard token overlap between two entity names."""
    ta = set(_normalize(tok) for tok in a.lower().split() if tok)
    tb = set(_normalize(tok) for tok in b.lower().split() if tok)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _match_score(candidate: dict, entity_name: str, state: str) -> float:
    """Score a HRSA search result against the queried entity."""
    score = 0.0
    cname = candidate.get("name", "") or candidate.get("organizationName", "")
    overlap = _name_overlap(cname, entity_name)
    score += overlap * 2.0
    cstate = (candidate.get("stateCode", "") or "").upper()
    if state and cstate == state.upper():
        score += 0.5
    return score


def lookup(entity_name: str, city: str, state: str) -> dict:
    """Return an HRSA profile dict for an FQHC entity.

    Keys returned:
        found (bool): True if a matching HRSA record was located.
        is_330 (bool | None): True = HRSA Section 330 grantee.
        is_lookalike (bool | None): True = HRSA-designated look-alike.
        site_count (int | None): Number of sites from HRSA data.
        service_lines (list[str]): Attested HRSA service lines (e.g. "Medical").
        languages (list[str]): Languages served per HRSA record.
        quality_recognition (list[str]): HRSA quality badges/awards.
        uds_reported (bool | None): True if UDS participation is indicated.
        health_center_name (str | None): Canonical HRSA name.
        hrsa_id (str | None): HRSA health center ID.
        source_url (str): URL used for the lookup.
    """
    default: dict = {
        "found": False,
        "is_330": None,
        "is_lookalike": None,
        "site_count": None,
        "service_lines": [],
        "languages": [],
        "quality_recognition": [],
        "uds_reported": None,
        "health_center_name": None,
        "hrsa_id": None,
        "source_url": "",
    }

    # Search by state code first (smaller result set)
    qs = urllib.parse.urlencode({"stateCode": state, "name": entity_name[:50]})
    url = f"{_HRSA_API_BASE}/search?{qs}"
    default["source_url"] = url

    data = _get(url)

    # The API may return a list or a dict with a results key
    candidates: list = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ("results", "data", "items", "healthCenters"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break

    if not candidates:
        # Fallback: city-name search
        qs2 = urllib.parse.urlencode({"city": city, "stateCode": state})
        url2 = f"{_HRSA_API_BASE}/search?{qs2}"
        data2 = _get(url2)
        if isinstance(data2, list):
            candidates = data2
        elif isinstance(data2, dict):
            for key in ("results", "data", "items", "healthCenters"):
                if isinstance(data2.get(key), list):
                    candidates = data2[key]
                    break

    if not candidates:
        return default

    # Find best match
    scored = sorted(
        [(c, _match_score(c, entity_name, state)) for c in candidates],
        key=lambda x: x[1], reverse=True,
    )
    best, best_score = scored[0]
    if best_score < 0.3:
        return default

    # Parse the matched record — field names vary by API version
    def _get_field(*keys):
        for k in keys:
            v = best.get(k)
            if v is not None:
                return v
        return None

    program_type = str(_get_field("programType", "program_type", "granteeType") or "").lower()
    is_330 = "grant" in program_type or "330" in program_type
    is_lookalike = "look" in program_type or "lookalike" in program_type

    site_count_raw = _get_field("siteCount", "numberOfSites", "totalSites")
    site_count: Optional[int] = None
    try:
        site_count = int(site_count_raw) if site_count_raw is not None else None
    except (TypeError, ValueError):
        pass

    # Service lines may appear as a list or comma-separated string
    sl_raw = _get_field("serviceLines", "services", "servicesOffered") or []
    if isinstance(sl_raw, str):
        sl_raw = [s.strip() for s in sl_raw.split(",") if s.strip()]
    service_lines: list[str] = [str(s) for s in sl_raw if s]

    lang_raw = _get_field("languages", "languagesServed", "languageList") or []
    if isinstance(lang_raw, str):
        lang_raw = [l.strip() for l in lang_raw.split(",") if l.strip()]
    languages: list[str] = [str(l) for l in lang_raw if l]

    qual_raw = _get_field("qualityRecognition", "awards", "qualityBadges") or []
    if isinstance(qual_raw, str):
        qual_raw = [q.strip() for q in qual_raw.split(",") if q.strip()]
    quality_recognition: list[str] = [str(q) for q in qual_raw if q]

    uds_raw = _get_field("udsReported", "udsParticipant", "uds")
    uds_reported: Optional[bool] = bool(uds_raw) if uds_raw is not None else None

    hc_name = str(_get_field("organizationName", "name", "healthCenterName") or "").strip() or None
    hrsa_id = str(_get_field("id", "healthCenterId", "bphcId") or "").strip() or None

    return {
        "found": True,
        "is_330": is_330,
        "is_lookalike": is_lookalike,
        "site_count": site_count,
        "service_lines": service_lines,
        "languages": languages,
        "quality_recognition": quality_recognition,
        "uds_reported": uds_reported,
        "health_center_name": hc_name,
        "hrsa_id": hrsa_id,
        "source_url": url,
    }
