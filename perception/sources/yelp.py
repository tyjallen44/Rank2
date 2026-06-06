from __future__ import annotations

from datetime import date
from typing import Iterator

import httpx

from ..config import settings
from ..models import EntitySummary, Review, ReviewSource

_BASE = "https://api.yelp.com/v3"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.yelp_api_key}"}


def search_business_id(name: str, city: str, state: str) -> str | None:
    params = {"term": name, "location": f"{city}, {state}", "limit": 1, "categories": "health"}
    r = httpx.get(f"{_BASE}/businesses/search", headers=_headers(), params=params)
    r.raise_for_status()
    businesses = r.json().get("businesses", [])
    return businesses[0]["id"] if businesses else None


def fetch_reviews(entity_id: str, business_id: str) -> Iterator[Review]:
    r = httpx.get(f"{_BASE}/businesses/{business_id}/reviews", headers=_headers())
    r.raise_for_status()
    for raw in r.json().get("reviews", []):
        yield Review(
            source=ReviewSource.yelp,
            entity_id=entity_id,
            review_id=raw["id"],
            author=raw.get("user", {}).get("name"),
            rating=raw.get("rating"),
            text=raw.get("text"),
            review_date=date.fromisoformat(raw["time_created"][:10]) if raw.get("time_created") else None,
        )


def fetch_summary(entity_id: str, business_id: str) -> EntitySummary:
    r = httpx.get(f"{_BASE}/businesses/{business_id}", headers=_headers())
    r.raise_for_status()
    data = r.json()
    return EntitySummary(
        entity_id=entity_id,
        source=ReviewSource.yelp,
        avg_rating=data.get("rating"),
        review_count=data.get("review_count", 0),
        as_of=date.today(),
    )
