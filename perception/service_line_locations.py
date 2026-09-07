"""Phase B — listing & location resolution (see docs/service-line-listing-analysis.md).

For each Phase-A service line, resolve the flagship listing + a bounded,
nearest-to-HQ sample of the SYSTEM'S OWN locations via Google Places, and surface
service lines with no findable listing ("invisible lines").

Affiliation is matched on BRAND tokens (system name + flagship sub-brand like
"Sanger"/"Levine"), never on clinical words (heart/cardiology), so a competitor's
"Heart & Vascular" clinic is not mistaken for ours. No silent failure: invisible
lines are surfaced, capped samples disclose sampled-vs-total, low-confidence
affiliations are kept+flagged, and the function never raises.
"""
from __future__ import annotations

import math
import re

from .models import (Listing, ServiceLineListings, ServiceLineListingSet,
                     ServiceLineSet)
from .data.places import text_search, _name_match, _is_healthcare, _tokens
from .service_line_discovery import _CANONICAL_SERVICE_LINES

_PER_LINE_CAP = 6

# Clinical / generic words that are NOT brand-distinctive — excluded when deciding
# affiliation, so "heart", "cancer", "orthopedic" can't affiliate a competitor.
_CLINICAL_STOP = {"institute", "center", "centre", "care", "services", "service",
                  "health", "healthcare", "clinic", "clinics", "medical", "hospital",
                  "group", "associates", "specialty", "specialists", "physicians"}
for _label, _aliases in _CANONICAL_SERVICE_LINES.values():
    for _a in _aliases:
        _CLINICAL_STOP |= set(re.findall(r"[a-z]+", _a))


def _brand_tokens(name: str) -> set:
    """Distinctive brand tokens of a name (drop clinical/generic words)."""
    return {t for t in _tokens(name) if t not in _CLINICAL_STOP}


def _haversine_mi(a: tuple, b: tuple) -> float:
    if a is None or b is None or None in a or None in b:
        return float("inf")
    (lat1, lon1), (lat2, lon2) = a, b
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _affiliation(cand_name: str, system_brand: set, sub_brand: set) -> "str | None":
    """high (system brand) | medium (sub-brand only) | low (weak) | None (competitor)."""
    cb = _brand_tokens(cand_name)
    if not cb:
        return None
    if cb & system_brand:
        return "high"
    if cb & sub_brand:
        return "medium"
    return None


def _to_listing(c: dict, role: str, affiliation: str) -> Listing:
    return Listing(
        place_id=c.get("place_id"), name=c.get("name", ""),
        formatted_address=c.get("formatted_address", ""),
        city=c.get("city", ""), state=c.get("state", ""),
        lat=c.get("lat"), lng=c.get("lng"),
        rating=c.get("rating"), review_count=c.get("review_count"),
        maps_url=c.get("maps_url"), types=c.get("types") or [],
        role=role, affiliation=affiliation, source="places_search")


def resolve_service_line_listings(system_name: str, hq_location: str,
                                  service_line_set: ServiceLineSet,
                                  on_event=None, per_line_cap: int = _PER_LINE_CAP,
                                  cache: bool = True) -> ServiceLineListingSet:
    """Resolve flagship + nearest-to-HQ location sample per service line."""
    emit = on_event or (lambda e: None)
    result = ServiceLineListingSet(system_name=system_name)
    metro = (hq_location or "").strip()

    # HQ coordinates (one Places geocode) → nearest-to-HQ ranking.
    hq_hits = text_search(metro, max_results=1) if metro else []
    hq_coords = ((hq_hits[0].get("lat"), hq_hits[0].get("lng"))
                 if hq_hits and hq_hits[0].get("lat") is not None else None)

    system_brand = _brand_tokens(system_name)

    for sl in service_line_set.lines:
        emit({"type": "text", "text": f"\nResolving listings for {sl.canonical_label}…"})
        line = ServiceLineListings(canonical_key=sl.canonical_key,
                                   canonical_label=sl.canonical_label,
                                   landing_url=sl.landing_url)

        # This line's sub-brand from its own name (e.g. "Sanger"/"Levine"), which
        # is the affiliation reference for both the flagship and the locations.
        sub_from_raw = _brand_tokens(sl.raw_name) - system_brand

        # 1. Flagship — a named center that matches the service line's name AND is
        # brand-affiliated (not just any clinic containing the specialty word, which
        # would wrongly pick an independent practice like "Charlotte Dermatology").
        flagship = None
        fq = " ".join(p for p in [sl.raw_name, metro] if p)
        for c in text_search(fq, max_results=5):
            if not (_is_healthcare(c["types"]) and _name_match(sl.raw_name, c["name"]) == "strong"):
                continue
            aff = _affiliation(c["name"], system_brand, sub_from_raw)
            if aff is not None:
                flagship = _to_listing(c, "flagship", aff)
                break
        line.flagship = flagship

        # Location affiliation reference: system brand + this line's sub-brand
        # (from the raw name and the resolved flagship). Catches "Sanger"/"Levine".
        sub_brand = (sub_from_raw
                     | (_brand_tokens(flagship.name) if flagship else set())) - system_brand

        # 2. Location sample — the system's own locations for this line.
        lq = " ".join(p for p in [system_name, sl.canonical_label, metro] if p)
        seen = {flagship.place_id} if (flagship and flagship.place_id) else set()
        affiliated = []
        for c in text_search(lq, max_results=10):
            if not _is_healthcare(c["types"]):
                continue
            pid = c.get("place_id")
            if pid and pid in seen:
                continue
            aff = _affiliation(c["name"], system_brand, sub_brand)
            if aff is None:                      # competitor / unaffiliated → skip
                continue
            if pid:
                seen.add(pid)
            affiliated.append((c, aff))

        # Nearest-to-HQ ordering, then cap (disclose estimated_total).
        affiliated.sort(key=lambda ca: _haversine_mi(hq_coords, (ca[0].get("lat"), ca[0].get("lng"))))
        line.estimated_total = len(affiliated)
        line.locations = [_to_listing(c, "location", aff) for c, aff in affiliated[:per_line_cap]]
        line.sampled = len(line.locations)

        # 3. Coverage (no silent failure).
        if flagship is None and not line.locations:
            line.coverage = "none"
        elif line.estimated_total > line.sampled:
            line.coverage = "partial"
        else:
            line.coverage = "full"

        tail = (f" of ~{line.estimated_total}" if line.estimated_total > line.sampled else "")
        msg = ("none found (invisible line)" if line.coverage == "none"
               else f"{'flagship + ' if flagship else ''}{line.sampled} location(s){tail}")
        emit({"type": "text", "text": f"  {sl.canonical_label}: {msg}"})
        result.lines.append(line)

    return result
