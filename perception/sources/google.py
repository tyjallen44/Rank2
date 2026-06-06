from __future__ import annotations

from datetime import date, datetime
from typing import Iterator

import httpx

from ..config import settings
from ..models import EntitySummary, Review, ReviewSource

_BASE = "https://maps.googleapis.com/maps/api/place"


def search_place_id(name: str, address: str) -> str | None:
    """Find a Google Place ID for an entity by name + address."""
    params = {
        "input": f"{name} {address}",
        "inputtype": "textquery",
        "fields": "place_id",
        "key": settings.google_places_api_key,
    }
    r = httpx.get(f"{_BASE}/findplacefromtext/json", params=params)
    r.raise_for_status()
    candidates = r.json().get("candidates", [])
    return candidates[0]["place_id"] if candidates else None


def fetch_reviews(entity_id: str, place_id: str) -> Iterator[Review]:
    params = {
        "place_id": place_id,
        "fields": "rating,reviews",
        "key": settings.google_places_api_key,
    }
    r = httpx.get(f"{_BASE}/details/json", params=params)
    r.raise_for_status()
    result = r.json().get("result", {})
    for raw in result.get("reviews", []):
        yield Review(
            source=ReviewSource.google,
            entity_id=entity_id,
            review_id=f"google-{place_id}-{raw['time']}",
            author=raw.get("author_name"),
            rating=raw.get("rating"),
            text=raw.get("text"),
            review_date=date.fromtimestamp(raw["time"]) if "time" in raw else None,
        )


def fetch_summary(entity_id: str, place_id: str) -> EntitySummary:
    params = {
        "place_id": place_id,
        "fields": "rating,user_ratings_total",
        "key": settings.google_places_api_key,
    }
    r = httpx.get(f"{_BASE}/details/json", params=params)
    r.raise_for_status()
    result = r.json().get("result", {})
    return EntitySummary(
        entity_id=entity_id,
        source=ReviewSource.google,
        avg_rating=result.get("rating"),
        review_count=result.get("user_ratings_total", 0),
        as_of=date.today(),
    )
