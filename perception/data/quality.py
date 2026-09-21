"""Verified hospital quality signals fetched in code (no model involved):
CMS Care Compare overall star rating (public datastore API) and the Leapfrog Hospital
Safety Grade (leapfroggroup.org). Handed to the analysis prompt as evidence and used
to anchor the Outcomes & Safety pillar deterministically. Fail-soft everywhere."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import cms


def fetch_hospital_quality(name: str, city: str, state: str, *, timeout: float = 45.0) -> dict:
    """{cms_star, cms_rated (bool|None), cms_name, cms_facility_id, hospital_type,
        leapfrog_grade, leapfrog_checked (bool), notes: [..]}"""
    out = {"cms_star": None, "cms_rated": None, "cms_name": None, "cms_facility_id": None,
           "hospital_type": None, "leapfrog_grade": None, "leapfrog_checked": False, "notes": []}

    def _cms():
        try:
            hs = cms.list_hospitals(state, cities=[city], timeout=timeout)
            m = cms._best_match(name, city, hs) if hs else None
            if m is None:
                hs_all = cms.list_hospitals(state, timeout=timeout)
                m = cms._best_match(name, city, hs_all) if hs_all else None
            if m is None:
                out["notes"].append("CMS: no Care Compare record matched this name (not a Medicare-certified hospital, or a different legal name)")
                out["cms_rated"] = None
                return
            out.update({"cms_name": m.name, "cms_facility_id": m.facility_id, "hospital_type": m.hospital_type,
                        "cms_star": m.overall_rating, "cms_rated": m.overall_rating is not None})
            if m.overall_rating is None:
                out["notes"].append(f"CMS: {m.name} is on Care Compare but has no overall star rating")
        except Exception as exc:
            out["notes"].append(f"CMS lookup failed: {type(exc).__name__}")

    def _lf():
        try:
            from .leapfrog import fetch_leapfrog_grade
            g = fetch_leapfrog_grade(name, city, state)
            out["leapfrog_checked"] = True
            out["leapfrog_grade"] = g
            if not g:
                out["notes"].append("Leapfrog: automated lookup found no grade for this exact name (may still be rated under another name)")
        except Exception as exc:
            out["notes"].append(f"Leapfrog lookup failed: {type(exc).__name__}")

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(lambda f: f(), [_cms, _lf]))
    return out


def evidence_lines(q: dict) -> str:
    """Evidence-block text for the prompt."""
    if not q:
        return ""
    lines = ["", "=== Verified quality signals (fetched from the source systems — USE VERBATIM) ==="]
    if q.get("cms_facility_id"):
        star = q.get("cms_star")
        lines.append(f"CMS Care Compare overall star rating: {str(star) + '★' if star else 'not rated'} "
                     f"(facility {q['cms_facility_id']}, listed as \"{q.get('cms_name')}\", {q.get('hospital_type') or 'hospital'})")
    else:
        lines.append("CMS Care Compare overall star rating: no matching Care Compare record")
    if q.get("leapfrog_grade"):
        lines.append(f"Leapfrog Hospital Safety Grade: {q['leapfrog_grade']} (verified on leapfroggroup.org, current cycle)")
    elif q.get("leapfrog_checked"):
        lines.append("Leapfrog Hospital Safety Grade: our automated lookup did not find a grade for this exact name — "
                     "if you can confirm the current grade on hospitalsafetygrade.org, report it; otherwise say 'not rated'.")
    for n in q.get("notes") or []:
        lines.append(f"Note: {n}")
    lines.append("Do not substitute other values for these two signals; if you find a conflicting number online, mention the conflict but score from the verified values.")
    return "\n".join(lines) + "\n"


def sources(q: dict) -> list:
    """Source entries for the report's 'Sources Consulted' box."""
    src = []
    if q and q.get("cms_facility_id"):
        src.append({"url": f"https://www.medicare.gov/care-compare/details/hospital/{q['cms_facility_id']}",
                    "title": f"CMS Care Compare — {q.get('cms_name')} (overall rating {q.get('cms_star') or 'not rated'})",
                    "domain": "medicare.gov", "cited": True, "verified": True})
    if q and q.get("leapfrog_grade"):
        src.append({"url": "https://www.hospitalsafetygrade.org/", "title": f"Leapfrog Hospital Safety Grade — {q['leapfrog_grade']}",
                    "domain": "hospitalsafetygrade.org", "cited": True, "verified": True})
    return src
