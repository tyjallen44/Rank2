"""Listing quality — is each listing right, not just present.

Runs after the roster is resolved and the Google profiles are read (server `_profile_audit`), on
data the run already holds: the website's office list (address, phone), each office's Google
profile (address, phone, category, status, latest review), the NPI registry's organizational
addresses, and each physician's Google profile address. Everything is measured; nothing here
changes a score. Output feeds the Locations / Physicians sections and the delivery check.

Checks per office
  phone      website phone == Google phone (last ten digits)
  address    website address == Google address (street number + street + ZIP)
  npi        the NPI registry lists an organizational address at this office
  category   Google primary category; consistency across offices is the finding
  status     OPERATIONAL vs closed profiles still live
  recency    days since the latest Google review on the profile
Plus duplicates (a second Google profile at an office's address) and, for physicians, whether
each Google profile points at one of the practice's offices.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Optional

from .location_resolver import same_office

STALE_REVIEW_DAYS = 90
_GENERIC_TYPES = {"doctor", "health", "point_of_interest", "establishment", "medical_clinic", "medical_office", "office", "store", "corporate_office"}


def _digits(phone: Optional[str]) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else d


def _days_since(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        return (date.today() - datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()).days
    except Exception:
        return None


def nppes_org_addresses(org_name: str, state: str) -> list[dict]:
    """Organizational (type-2) NPI records for the brand in the state: [{address, city, state, zip, phone, name}]."""
    try:
        from .physician_discovery import _nppes_get
    except Exception:
        return []
    out: list[dict] = []
    org = (org_name or "").strip()
    if not org:
        return out
    for rec in _nppes_get({"enumeration_type": "NPI-2", "organization_name": f"{org}*", "state": (state or "").upper()[:2], "limit": 200}):
        for a in rec.get("addresses") or []:
            if a.get("address_purpose") != "LOCATION":
                continue
            street = (a.get("address_1") or "").strip()
            if not street:
                continue
            full = f"{street}, {(a.get('city') or '').strip().title()}, {(a.get('state') or '').strip().upper()} {(a.get('postal_code') or '')[:5]}"
            out.append({"address": full, "city": (a.get("city") or "").strip().title(), "state": (a.get("state") or "").strip().upper(),
                        "zip": (a.get("postal_code") or "")[:5], "phone": a.get("telephone_number") or "",
                        "name": ((rec.get("basic") or {}).get("organization_name") or "").strip()})
    return out


def audit(*, offices: list[dict], profiles: list[dict], nppes: list[dict], physicians: list[dict], duplicates: list[dict] | None = None) -> dict:
    """offices: [{name, address, phone, place_id, is_anchor}] (website phone/address when known);
    profiles: [{place_id, name, address, phone, primary_type, status, last_review_at, review_count, found}];
    nppes: nppes_org_addresses(); physicians: [{name, google_address}] (google_address None = no profile)."""
    prof_by_pid = {p.get("place_id"): p for p in profiles if p.get("place_id")}
    rows: list[dict] = []
    for o in offices:
        p = prof_by_pid.get(o.get("place_id")) or {}
        wp, gp = _digits(o.get("phone")), _digits(p.get("phone"))
        wa, ga = (o.get("website_address") or o.get("address") or ""), (p.get("address") or "")
        row = {
            "name": o.get("name"), "place_id": o.get("place_id"), "is_anchor": bool(o.get("is_anchor")),
            "website_phone": o.get("phone") or "", "google_phone": p.get("phone") or "",
            "phone_match": (wp == gp) if (wp and gp) else None,
            "website_address": wa, "google_address": ga,
            "address_match": same_office(wa, ga) if (wa and ga and "website" in (o.get("sources") or [])) else None,
            "npi_listed": any(same_office(wa or ga, n["address"]) for n in nppes) if (wa or ga) and nppes else None,
            "category": p.get("primary_type") or "", "status": p.get("status") or "",
            "review_count": p.get("review_count"), "last_review_days": _days_since(p.get("last_review_at")),
        }
        rows.append(row)
    phone_cmp = [r for r in rows if r["phone_match"] is not None]
    addr_cmp = [r for r in rows if r["address_match"] is not None]
    npi_cmp = [r for r in rows if r["npi_listed"] is not None]
    cats = Counter(r["category"] for r in rows if r["category"])
    closed = [r["name"] for r in rows if r["status"] and r["status"] != "OPERATIONAL"]
    rec_cmp = [r for r in rows if r["last_review_days"] is not None]
    stale = [r["name"] for r in rec_cmp if r["last_review_days"] > STALE_REVIEW_DAYS]
    no_reviews = [r["name"] for r in rows if r["place_id"] and (r["review_count"] or 0) == 0]

    phys_with = [p for p in physicians if p.get("google_address")]
    office_addrs = [r["google_address"] or r["website_address"] for r in rows if (r["google_address"] or r["website_address"])]
    at_office = [p for p in phys_with if any(same_office(p["google_address"], a) for a in office_addrs)]
    elsewhere = [p.get("name") for p in phys_with if p not in at_office]

    return {
        "offices": rows,
        "phone": {"compared": len(phone_cmp), "match": sum(1 for r in phone_cmp if r["phone_match"]),
                  "mismatch": [{"name": r["name"], "website": r["website_phone"], "google": r["google_phone"]} for r in phone_cmp if not r["phone_match"]]},
        "address": {"compared": len(addr_cmp), "match": sum(1 for r in addr_cmp if r["address_match"]),
                    "mismatch": [{"name": r["name"], "website": r["website_address"], "google": r["google_address"]} for r in addr_cmp if not r["address_match"]]},
        "npi": {"compared": len(npi_cmp), "listed": sum(1 for r in npi_cmp if r["npi_listed"]), "missing": [r["name"] for r in npi_cmp if not r["npi_listed"]],
                "records": len(nppes)},
        "category": {"counts": dict(cats.most_common()), "consistent": len(cats) <= 1,
                     "generic": [r["name"] for r in rows if r["category"] and r["category"] in _GENERIC_TYPES]},
        "status": {"closed": closed},
        "duplicates": list(duplicates or []),
        "recency": {"compared": len(rec_cmp), "fresh": len(rec_cmp) - len(stale), "stale": stale, "no_reviews": no_reviews, "days": STALE_REVIEW_DAYS},
        "physicians": {"total": len(physicians), "with_google": len(phys_with), "at_office": len(at_office), "elsewhere": elsewhere},
    }


def issues(q: dict) -> list[str]:
    """Short plain sentences for the report and the group page; empty when everything checks out."""
    out: list[str] = []
    ph, ad, npi, cat, st, dup, rec, phy = (q.get(k) or {} for k in ("phone", "address", "npi", "category", "status", "duplicates", "recency", "physicians"))
    if ph.get("mismatch"):
        out.append(f"{len(ph['mismatch'])} office{'s' if len(ph['mismatch']) != 1 else ''} where the Google phone differs from the website: "
                   + "; ".join(f"{m['name']} (site {m['website']}, Google {m['google']})" for m in ph["mismatch"][:4]) + ".")
    if ad.get("mismatch"):
        out.append(f"{len(ad['mismatch'])} office{'s' if len(ad['mismatch']) != 1 else ''} where the Google address differs from the website: "
                   + "; ".join(m["name"] for m in ad["mismatch"][:6]) + ".")
    if npi.get("missing") and npi.get("records"):
        out.append(f"{len(npi['missing'])} office{'s' if len(npi['missing']) != 1 else ''} with no organizational NPI address in the registry: " + ", ".join(npi["missing"][:6]) + ".")
    if cat.get("counts") and not cat.get("consistent"):
        out.append("Offices use " + str(len(cat["counts"])) + " different Google categories (" + ", ".join(f"{k.replace('_', ' ')} ×{v}" for k, v in list(cat["counts"].items())[:4]) + ") — pick one.")
    if cat.get("generic"):
        out.append(f"{len(cat['generic'])} office{'s' if len(cat['generic']) != 1 else ''} categorized generically on Google (" + ", ".join(cat["generic"][:5]) + ") rather than by specialty.")
    if st.get("closed"):
        out.append(f"{len(st['closed'])} Google profile{'s' if len(st['closed']) != 1 else ''} marked closed still live: " + ", ".join(st["closed"][:5]) + ".")
    if isinstance(dup, list) and dup:
        out.append(f"{len(dup)} second Google profile{'s' if len(dup) != 1 else ''} at an office's address: " + "; ".join(f"“{d.get('name')}” at {d.get('of')}" for d in dup[:4]) + ".")
    if rec.get("stale"):
        out.append(f"{len(rec['stale'])} office{'s' if len(rec['stale']) != 1 else ''} with no Google review in {rec.get('days', STALE_REVIEW_DAYS)} days: " + ", ".join(rec["stale"][:6]) + ".")
    if rec.get("no_reviews"):
        out.append(f"{len(rec['no_reviews'])} office profile{'s' if len(rec['no_reviews']) != 1 else ''} with no reviews at all: " + ", ".join(rec["no_reviews"][:6]) + ".")
    if phy.get("elsewhere"):
        out.append(f"{len(phy['elsewhere'])} physician Google profile{'s' if len(phy['elsewhere']) != 1 else ''} pointing at an address that is not one of the practice's offices: "
                   + ", ".join(phy["elsewhere"][:6]) + ".")
    return out


def summary_line(q: dict) -> str:
    ph, ad, npi, cat, rec, phy = (q.get(k) or {} for k in ("phone", "address", "npi", "category", "recency", "physicians"))
    parts = []
    if ph.get("compared"):
        parts.append(f"phone matches the website on {ph['match']} of {ph['compared']} profiles")
    if ad.get("compared"):
        parts.append(f"address matches on {ad['match']} of {ad['compared']}")
    if npi.get("compared") and npi.get("records"):
        parts.append(f"NPI registry lists {npi['listed']} of {npi['compared']} offices")
    if cat.get("counts"):
        parts.append("one Google category across offices" if cat.get("consistent") else f"{len(cat['counts'])} Google categories across offices")
    if rec.get("compared"):
        parts.append(f"{rec['fresh']} of {rec['compared']} reviewed in the last {rec.get('days', STALE_REVIEW_DAYS)} days")
    if phy.get("total"):
        parts.append(f"{phy['with_google']} of {phy['total']} physicians have a Google profile" + (f", {phy['at_office']} at a practice office" if phy["with_google"] else ""))
    return "; ".join(parts)
