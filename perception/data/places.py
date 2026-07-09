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

import json
import os
import re
import time
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


# --- shared Places transport: pacing + 429 backoff + in-run cache -----------
# Market runs fire several Places calls per provider across the fetchers below
# with no spacing; bursts trip Google's PER-MINUTE quota and every fetcher
# hard-fails on the first 429 (observed on the shared key 2026-07-09 while the
# daily quota was fine). Three rules: pace real calls, retry transient 429/5xx
# honoring Retry-After, and answer identical requests from an in-run cache.
# After the retry budget the original exceptions propagate unchanged.
_MIN_INTERVAL_S = 0.12
_MAX_ATTEMPTS = 3
_RETRYABLE = {429, 500, 502, 503, 504}
_places_cache: dict = {}
_last_call_ts = [0.0]


def clear_places_cache() -> None:
    """Reset the in-process response cache (tests / long-lived servers)."""
    _places_cache.clear()


def _places_search(key: str, field_mask: str, payload: dict, timeout: float) -> list:
    """POST to Places searchText with pacing, 429/5xx backoff, and caching."""
    cache_key = (field_mask, json.dumps(payload, sort_keys=True))
    if cache_key in _places_cache:
        return _places_cache[cache_key]
    attempt = 0
    while True:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call_ts[0])
        if wait > 0:
            time.sleep(wait)
        _last_call_ts[0] = time.monotonic()
        resp = httpx.post(
            _SEARCH_TEXT,
            headers={"Content-Type": "application/json",
                     "X-Goog-Api-Key": key, "X-Goog-FieldMask": field_mask},
            json=payload,
            timeout=timeout,
        )
        attempt += 1
        status_code = getattr(resp, "status_code", None)
        if status_code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
            retry_after = (getattr(resp, "headers", None) or {}).get("Retry-After")
            try:
                delay = min(float(retry_after), 10.0) if retry_after else 1.5 ** attempt
            except ValueError:
                delay = 1.5 ** attempt
            time.sleep(delay)
            continue
        resp.raise_for_status()
        places = resp.json().get("places", [])
        _places_cache[cache_key] = places
        return places


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
        places = _places_search(
            key,
            "places.displayName,places.rating,places.userRatingCount,places.businessStatus",
            {"textQuery": query, "pageSize": 1},
            timeout,
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:400] if exc.response else ""
        return GoogleRead(query=query, verified=False, reason=f"Places lookup failed: {exc} | {body}")
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
        places = _places_search(
            key,
            "places.displayName,places.rating,places.userRatingCount,places.businessStatus",
            {"textQuery": query, "pageSize": max_results},
            timeout,
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:400] if exc.response else ""
        return (
            GoogleRead(query=query, verified=False, reason=f"Places lookup failed: {exc} | {body}"),
            Footprint(query=query, note="footprint sample unavailable"),
        )
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
        places = _places_search(
            key,
            "places.id,places.displayName,places.rating,places.userRatingCount",
            {"textQuery": query, "pageSize": min(max_results, 20)},
            timeout,
        )
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


def search_entity_candidates(
    name: str,
    city: str | None = None,
    state: str | None = None,
    *,
    api_key: str | None = None,
    max_results: int = 5,
    timeout: float = 20.0,
) -> list[dict]:
    """Search Google Places for candidates matching name + location.

    Returns up to max_results dicts with keys: name, address, rating, review_count.
    Used by the search-before-analyze flow on the Individual Report page.
    """
    query = " ".join(p for p in (name, city, state) if p).strip()
    key = _api_key(api_key)
    if not key:
        return []
    try:
        raw = _places_search(
            key,
            "places.displayName,places.formattedAddress,places.rating,places.userRatingCount",
            {"textQuery": query, "pageSize": min(max_results, 20)},
            timeout,
        )
    except (httpx.HTTPError, ValueError):
        return []
    return [
        {
            "name": (p.get("displayName") or {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "rating": p.get("rating"),
            "review_count": p.get("userRatingCount"),
        }
        for p in raw
    ]


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
        places = _places_search(
            key,
            "places.displayName,places.rating",
            {"textQuery": query, "pageSize": max_results},
            timeout,
        )
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
