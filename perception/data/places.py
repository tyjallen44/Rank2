"""Google Places API v1 client — the authoritative Google rating + review count.

This is the single most important reputation signal in a Rank2 report, and the
methodology requires the *real* Google number, never a stand-in. We use the
Places ``searchText`` endpoint:

- ``fetch_google_rating`` resolves one provider to its primary listing and
  returns a verified rating + review count (with a name-match confidence so we
  never silently report a different business).
- ``fetch_footprint`` does one broader search and returns the breadth + rating
  range across a system's listings (sampled, not a census).

A Google Places API key is required (``settings.google_places_api_key`` or the
``GOOGLE_PLACES_API_KEY`` env var). With no key, calls return an unverified read
with a clear reason rather than raising.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..config import settings

_SEARCH_TEXT = "https://places.googleapis.com/v1/places:searchText"

# Common corporate/health tokens that shouldn't count toward a name match —
# almost every hospital name contains some of these, so matching on them alone
# would let "Anytown Medical Center" masquerade as the intended provider.
_STOPWORDS = {
    "the", "of", "and", "at", "for", "health", "healthcare", "hospital",
    "hospitals", "medical", "center", "centre", "system", "systems", "clinic",
    "clinics", "care", "group", "associates", "institute", "regional",
    "memorial", "saint", "st", "university", "department", "services",
}


@dataclass
class GoogleRead:
    """A single resolved Google Business Profile read."""

    query: str
    verified: bool
    rating: Optional[float] = None
    review_count: Optional[int] = None
    matched_name: Optional[str] = None
    name_match: str = "none"          # "strong" | "weak" | "none"
    business_status: Optional[str] = None
    reason: Optional[str] = None      # populated when verified is False

    def as_line(self) -> str:
        """One-line human/LLM-readable summary for the evidence block."""
        if self.verified and self.rating is not None:
            return (
                f"{self.rating:.1f}★ · {self.review_count or 0} reviews "
                f"(matched listing: {self.matched_name})"
            )
        return f"not verified — {self.reason or 'no rated listing found'}"


@dataclass
class Footprint:
    """A sampled breadth read across a system/group's many listings."""

    query: str
    listings_sampled: int = 0
    rating_low: Optional[float] = None
    rating_high: Optional[float] = None
    note: str = ""

    def as_line(self) -> str:
        if self.listings_sampled <= 1:
            return self.note or "single listing"
        rng = ""
        if self.rating_low is not None and self.rating_high is not None:
            rng = f", ratings {self.rating_low:.1f}–{self.rating_high:.1f}"
        return (
            f"~{self.listings_sampled} listings sampled{rng} "
            f"(sampled, not a census)"
        )


def _api_key(explicit: str | None = None) -> str | None:
    return explicit or settings.google_places_api_key or os.environ.get("GOOGLE_PLACES_API_KEY") or None


def _tokens(name: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {t for t in raw if t not in _STOPWORDS and len(t) > 1}


def _name_match(requested: str, found: str) -> str:
    """Classify how well a returned listing name matches what we asked for."""
    a, b = _tokens(requested), _tokens(found)
    if not a or not b:
        return "weak"
    overlap = len(a & b) / len(a)
    if overlap >= 0.6:
        return "strong"
    if overlap >= 0.3:
        return "weak"
    return "none"


def fetch_google_rating(
    name: str,
    city: str | None = None,
    state: str | None = None,
    *,
    api_key: str | None = None,
    timeout: float = 20.0,
) -> GoogleRead:
    """Resolve one provider to its primary Google listing.

    Returns a verified read only when a rated listing is found AND its name
    plausibly matches the request. A weak/none match comes back ``verified=False``
    with the candidate named in ``reason`` so the caller never reports a
    different business as if it were the provider.
    """
    query = " ".join(p for p in (name, city, state) if p).strip()
    key = _api_key(api_key)
    if not key:
        return GoogleRead(query=query, verified=False, reason="Places API key not configured")

    try:
        resp = httpx.post(
            _SEARCH_TEXT,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": (
                    "places.displayName,places.rating,"
                    "places.userRatingCount,places.businessStatus"
                ),
            },
            json={"textQuery": query, "pageSize": 1},
            timeout=timeout,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except (httpx.HTTPError, ValueError) as exc:
        return GoogleRead(query=query, verified=False, reason=f"Places lookup failed: {exc}")

    if not places:
        return GoogleRead(query=query, verified=False, reason="no listing returned by Places")

    top = places[0]
    found_name = (top.get("displayName") or {}).get("text", "")
    rating = top.get("rating")
    count = top.get("userRatingCount")
    match = _name_match(name, found_name)

    if rating is None:
        return GoogleRead(
            query=query, verified=False, matched_name=found_name, name_match=match,
            reason=f"listing '{found_name}' has no Google rating",
        )
    if match == "none":
        return GoogleRead(
            query=query, verified=False, matched_name=found_name, name_match=match,
            reason=f"closest listing '{found_name}' is a weak match — likely a different business",
        )

    return GoogleRead(
        query=query,
        verified=True,
        rating=float(rating),
        review_count=int(count) if count is not None else 0,
        matched_name=found_name,
        name_match=match,
        business_status=top.get("businessStatus"),
    )


def fetch_provider(
    name: str,
    city: str | None = None,
    state: str | None = None,
    *,
    api_key: str | None = None,
    max_results: int = 8,
    timeout: float = 20.0,
) -> tuple[GoogleRead, Footprint]:
    """One Places call → both the front-door read and a footprint sample.

    Efficient path for the per-provider enrichment pass: the top result (when it
    matches) is the front door; the full result set gives the footprint range.
    """
    query = " ".join(p for p in (name, city, state) if p).strip()
    key = _api_key(api_key)
    if not key:
        return (
            GoogleRead(query=query, verified=False, reason="Places API key not configured"),
            Footprint(query=query, note="footprint not sampled (no API key)"),
        )

    try:
        resp = httpx.post(
            _SEARCH_TEXT,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": (
                    "places.displayName,places.rating,"
                    "places.userRatingCount,places.businessStatus"
                ),
            },
            json={"textQuery": query, "pageSize": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except (httpx.HTTPError, ValueError) as exc:
        return (
            GoogleRead(query=query, verified=False, reason=f"Places lookup failed: {exc}"),
            Footprint(query=query, note="footprint sample unavailable"),
        )

    if not places:
        return (
            GoogleRead(query=query, verified=False, reason="no listing returned by Places"),
            Footprint(query=query, note="no listings found"),
        )

    top = places[0]
    found_name = (top.get("displayName") or {}).get("text", "")
    rating = top.get("rating")
    count = top.get("userRatingCount")
    match = _name_match(name, found_name)

    if rating is None or match == "none":
        reason = (
            f"closest listing '{found_name}' is a weak match — likely a different business"
            if rating is not None else
            f"listing '{found_name}' has no Google rating"
        )
        read = GoogleRead(query=query, verified=False, matched_name=found_name,
                          name_match=match, reason=reason)
    else:
        read = GoogleRead(
            query=query, verified=True, rating=float(rating),
            review_count=int(count) if count is not None else 0,
            matched_name=found_name, name_match=match,
            business_status=top.get("businessStatus"),
        )

    ratings = [p["rating"] for p in places if p.get("rating") is not None]
    footprint = Footprint(
        query=query,
        listings_sampled=len(places),
        rating_low=min(ratings) if ratings else None,
        rating_high=max(ratings) if ratings else None,
    )
    return read, footprint


@dataclass
class Listing:
    """A single Google listing with an identity, for census/dedup work."""
    place_id: str
    name: str
    rating: Optional[float] = None
    review_count: Optional[int] = None


def search_listings(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = 20,
    timeout: float = 20.0,
) -> list[Listing]:
    """Return up to ``max_results`` rated listings for a query, with place_ids.

    Used by the system-reputation aggregator to enumerate a system's locations
    and dedupe them by place_id before the review-count-weighted blend.
    """
    key = _api_key(api_key)
    if not key:
        return []
    try:
        resp = httpx.post(
            _SEARCH_TEXT,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.rating,places.userRatingCount"
                ),
            },
            json={"textQuery": query, "pageSize": min(max_results, 20)},
            timeout=timeout,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except (httpx.HTTPError, ValueError):
        return []
    out: list[Listing] = []
    for p in places:
        pid = p.get("id")
        if not pid or p.get("rating") is None:
            continue
        out.append(Listing(
            place_id=pid,
            name=(p.get("displayName") or {}).get("text", ""),
            rating=float(p["rating"]),
            review_count=int(p.get("userRatingCount") or 0),
        ))
    return out


def fetch_footprint(
    org: str,
    city: str | None = None,
    state: str | None = None,
    *,
    api_key: str | None = None,
    max_results: int = 8,
    timeout: float = 20.0,
) -> Footprint:
    """One broad search returning breadth + rating range across listings."""
    query = " ".join(p for p in (org, city, state) if p).strip()
    key = _api_key(api_key)
    if not key:
        return Footprint(query=query, note="footprint not sampled (no API key)")

    try:
        resp = httpx.post(
            _SEARCH_TEXT,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.displayName,places.rating",
            },
            json={"textQuery": query, "pageSize": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except (httpx.HTTPError, ValueError):
        return Footprint(query=query, note="footprint sample unavailable")

    ratings = [p["rating"] for p in places if p.get("rating") is not None]
    if not ratings:
        return Footprint(query=query, listings_sampled=len(places), note="no rated listings in sample")
    return Footprint(
        query=query,
        listings_sampled=len(places),
        rating_low=min(ratings),
        rating_high=max(ratings),
    )
