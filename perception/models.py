from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    doctor = "doctor"
    practice = "practice"
    hospital = "hospital"


class ReviewSource(str, Enum):
    google = "google"
    yelp = "yelp"
    healthgrades = "healthgrades"
    zocdoc = "zocdoc"
    cms = "cms"


class SentimentLabel(str, Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class Entity(BaseModel):
    id: str
    entity_type: EntityType
    name: str
    npi: Optional[str] = None          # National Provider Identifier
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class Review(BaseModel):
    source: ReviewSource
    entity_id: str
    review_id: str
    author: Optional[str] = None
    rating: Optional[float] = None     # normalized 1–5
    text: Optional[str] = None
    review_date: Optional[date] = None
    sentiment: Optional[SentimentLabel] = None
    sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0)


class EntitySummary(BaseModel):
    entity_id: str
    source: ReviewSource
    avg_rating: Optional[float] = None
    review_count: int = 0
    positive_pct: Optional[float] = None
    negative_pct: Optional[float] = None
    as_of: Optional[date] = None
