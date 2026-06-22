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
    specialty: Optional[str] = None    # e.g. "Orthopedics" — triggers focused analysis when set


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


class AffiliationType(str, Enum):
    independent = "independent"
    hospital_affiliated = "hospital_affiliated"
    unknown = "unknown"


class RankedProvider(BaseModel):
    rank: int
    name: str
    affiliation_type: AffiliationType = AffiliationType.unknown
    surgeon_count: Optional[str] = None   # e.g. "12", "~20", "3–5", or None if unknown
    overall_rating: str = ""               # e.g. "A" or "4.2/5 stars"
    key_strengths: list[str] = Field(default_factory=list)
    notable_weaknesses: list[str] = Field(default_factory=list)
    best_suited_for: str = ""
    recommendation_summary: str = ""


class AnalysisResult(BaseModel):
    """Structured result from a Claude-powered market analysis."""
    run_id: str
    location: str                          # e.g. "Mobile, Alabama"
    specialty: Optional[str] = None        # None → broad hospital analysis
    generated_at: date
    top_recommendation: str = ""
    practical_advice: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    rankings: list[RankedProvider] = Field(default_factory=list)
    report_markdown: str = ""              # full narrative report text
    pdf_path: Optional[str] = None
    md_path: Optional[str] = None
