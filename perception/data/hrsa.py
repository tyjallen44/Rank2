"""HRSA Health Center Program public data lookup.

Uses the actual HRSA Find a Health Center API:
  - Geocode: https://data.hrsa.gov/HDWLocatorApi/Geo/Geocode?text={city},{state}
  - Search:  https://data.hrsa.gov/HDWLocatorApi/HealthCenters/find?lon=&lat=&radius=50

The search endpoint returns site-level records. Presence in this database confirms
Section 330 grantee / look-alike status (it is the BPHC health center locator).
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


_API_BASE = "https://data.hrsa.gov/HDWLocatorApi"
_TIMEOUT = 10
_RADIUS_MILES = 50


def _ssl_ctx() -> ssl.SSLContext:
    """HRSA's cert has a non-critical CA constraint; skip verification."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(url: str) -> list | dict | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Rank2-Pulse/1.0 (health center research tool)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_ctx()) as resp:
            return json.loads(resp.read())
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


def _geocode(city: str, state: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city/state string, or None on failure."""
    text = urllib.parse.quote(f"{city}, {state}")
    data = _get(f"{_API_BASE}/Geo/Geocode?text={text}")
    if not isinstance(data, dict) or not data.get("issuccessful"):
        return None
    try:
        return float(data["latitude"]), float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def lookup(entity_name: str, city: str, state: str) -> dict:
    """Return an HRSA profile dict for an FQHC entity.

    Keys returned:
        found (bool): True if a matching HRSA record was located.
        is_330 (bool | None): True when the org appears in the HRSA HC locator
            (all records are 330 grantees or look-alikes by definition).
        is_lookalike (bool | None): Cannot be distinguished from this endpoint;
            always None.
        site_count (int | None): Number of sites found for the org in HRSA data.
        site_names (list[str]): Individual site names from HRSA records.
        service_lines (list[str]): Not available from this endpoint; always [].
        languages (list[str]): Not available from this endpoint; always [].
        quality_recognition (list[str]): Not available; always [].
        uds_reported (bool | None): Not available; always None.
        health_center_name (str | None): Canonical HRSA org name (ParentCtrNm).
        hrsa_id (str | None): Id of the first matching site record.
        website (str | None): URL from HRSA record.
        source_url (str): Search URL used.
    """
    default: dict = {
        "found": False,
        "is_330": None,
        "is_lookalike": None,
        "site_count": None,
        "site_names": [],
        "service_lines": [],
        "languages": [],
        "quality_recognition": [],
        "uds_reported": None,
        "health_center_name": None,
        "hrsa_id": None,
        "website": None,
        "source_url": "",
    }

    coords = _geocode(city, state)
    if coords is None:
        return default

    lat, lon = coords
    search_url = (
        f"{_API_BASE}/HealthCenters/find"
        f"?lon={lon}&lat={lat}&radius={_RADIUS_MILES}"
    )
    default["source_url"] = search_url

    sites: list = _get(search_url) or []
    if not isinstance(sites, list) or not sites:
        return default

    # Group sites by ParentCtrNm (org-level), score each org against entity_name
    orgs: dict[str, list] = {}
    for site in sites:
        parent = (site.get("ParentCtrNm") or "").strip()
        if not parent:
            parent = (site.get("CtrNm") or "").strip()
        if parent:
            orgs.setdefault(parent, []).append(site)

    if not orgs:
        return default

    # Pick the best-matching org
    scored = sorted(
        [(name, _name_overlap(name, entity_name), records)
         for name, records in orgs.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    best_name, best_score, best_sites = scored[0]

    if best_score < 0.25:
        return default

    first = best_sites[0]
    website_raw = (first.get("SiteUrl") or first.get("UrlTxt") or "").strip()
    website = website_raw if website_raw.startswith("http") else (
        f"https://{website_raw}" if website_raw else None
    )

    site_names = [s.get("CtrNm", "").strip() for s in best_sites if s.get("CtrNm")]

    return {
        "found": True,
        # Presence in this endpoint confirms 330 grantee or look-alike
        "is_330": True,
        "is_lookalike": None,
        "site_count": len(best_sites),
        "site_names": site_names,
        "service_lines": [],
        "languages": [],
        "quality_recognition": [],
        "uds_reported": None,
        "health_center_name": best_name,
        "hrsa_id": str(first.get("Id", "")),
        "website": website,
        "source_url": search_url,
    }
