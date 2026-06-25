"""System-wide weighted reputation — a review-count-weighted Google rating
across all of a system's locations (not a single flagship listing).

Mirrors the team's ``get_reputation_aggregate``: enumerate a system's locations
from the NPPES registry, resolve each to its Google listing, and blend the
ratings weighted by review volume. This is the authoritative reputation signal
for a multi-location system. Cost scales with location count, so it is capped
per system and cached (ratings move slowly).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import nppes, places
from .evidence import normalize_name

_CACHE_TTL_DAYS = 30
_GENERIC = {
    "health", "healthcare", "hospital", "medical", "center", "system", "clinic",
    "care", "group", "the", "of", "and", "regional", "inc", "llc", "pc",
}


@dataclass
class SystemReputation:
    org: str
    weighted_rating: Optional[float] = None
    total_reviews: int = 0
    location_count: int = 0
    confidence: str = "sample"   # "registry" (NPPES-enumerated) | "sample" (Places-only)
    capped: bool = False

    @property
    def available(self) -> bool:
        return self.weighted_rating is not None and self.location_count > 0

    def as_line(self) -> str:
        if not self.available:
            return ""
        label = "review-count-weighted" if self.total_reviews else "average"
        return (
            f"{self.weighted_rating:.1f}★ · {self.total_reviews:,} reviews across "
            f"{self.location_count}{'+' if self.capped else ''} locations ({label}"
            + (", registry-enumerated" if self.confidence == "registry" else ", sampled")
            + ")"
        )

    def to_dict(self) -> dict:
        return {
            "rating": self.weighted_rating,
            "total_reviews": self.total_reviews,
            "location_count": self.location_count,
            "confidence": self.confidence,
            "capped": self.capped,
        }


def _brand_tokens(org: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", org.lower()) if t not in _GENERIC and len(t) >= 4}


def _belongs(found_name: str, org: str) -> bool:
    """Guard so a per-location search doesn't fold in an unrelated business."""
    brand = _brand_tokens(org)
    if not brand:
        return True
    found = set(re.findall(r"[a-z0-9]+", (found_name or "").lower()))
    return len(brand & found) >= 1


def _blend(listings: list[places.Listing]) -> tuple[Optional[float], int]:
    """Review-count-weighted mean rating (falls back to simple mean if no counts)."""
    total = sum(l.review_count or 0 for l in listings)
    if total > 0:
        weighted = sum((l.rating or 0) * (l.review_count or 0) for l in listings)
        return round(weighted / total, 1), total
    rated = [l.rating for l in listings if l.rating is not None]
    return (round(sum(rated) / len(rated), 1) if rated else None), 0


def system_reputation(
    org: str,
    state: str | None = None,
    *,
    max_locations: int = 40,
    api_key: str | None = None,
    use_cache: bool = True,
) -> SystemReputation:
    """Compute the review-count-weighted Google rating across a system's locations."""
    cache_key = f"{normalize_name(org)}|{(state or '').upper()}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    locations = nppes.enumerate_org_locations(org, state)
    confidence = "registry" if locations else "sample"

    by_pid: dict[str, places.Listing] = {}
    if locations:
        capped = len(locations) > max_locations
        for city, st in locations[:max_locations]:
            for listing in places.search_listings(f"{org} {city} {st}", api_key=api_key, max_results=3):
                if _belongs(listing.name, org):
                    by_pid[listing.place_id] = listing
    else:
        capped = False
        query = f"{org} {state}" if state else org
        for listing in places.search_listings(query, api_key=api_key, max_results=20):
            if _belongs(listing.name, org):
                by_pid[listing.place_id] = listing

    listings = list(by_pid.values())
    rating, total = _blend(listings)
    rep = SystemReputation(
        org=org,
        weighted_rating=rating,
        total_reviews=total,
        location_count=len(listings),
        confidence=confidence,
        capped=capped,
    )
    if use_cache and rep.available:
        _cache_set(cache_key, rep)
    return rep


# ── Cache (DuckDB-backed; degrades to no-op if the DB is unavailable) ─────────

def _cache_get(key: str) -> Optional[SystemReputation]:
    try:
        from ..db import get_connection
        con = get_connection()
        row = con.execute(
            "SELECT payload, fetched_at FROM reputation_cache WHERE org_key = ?", [key]
        ).fetchone()
        con.close()
    except Exception:
        return None
    if not row:
        return None
    payload, fetched_at = row
    try:
        if date.fromisoformat(str(fetched_at)) < date.today() - timedelta(days=_CACHE_TTL_DAYS):
            return None
        d = json.loads(payload)
        return SystemReputation(
            org=d["org"], weighted_rating=d.get("rating"), total_reviews=d.get("total_reviews", 0),
            location_count=d.get("location_count", 0), confidence=d.get("confidence", "sample"),
            capped=d.get("capped", False),
        )
    except Exception:
        return None


def _cache_set(key: str, rep: SystemReputation) -> None:
    try:
        from ..db import get_connection
        con = get_connection()
        payload = json.dumps({"org": rep.org, **rep.to_dict()})
        con.execute(
            "INSERT OR REPLACE INTO reputation_cache (org_key, payload, fetched_at) VALUES (?, ?, ?)",
            [key, payload, date.today().isoformat()],
        )
        con.close()
    except Exception:
        pass
