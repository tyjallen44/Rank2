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


class SizeCategory(str, Enum):
    large = "large"
    community = "community"
    unknown = "unknown"


class ConsolidatedLocation(BaseModel):
    name: str
    overall_rating: str = ""


class TierScores(BaseModel):
    """The four AI Visibility tiers, each 0–100. The first slot is labeled
    'Outcomes & Safety' (procedural) or 'Quality & Coordination' (relationship)."""
    clinical_outcomes_safety: Optional[int] = None
    credentials_recognition: Optional[int] = None
    patient_experience_reviews: Optional[int] = None
    access_fit: Optional[int] = None

    def as_dict(self) -> dict[str, Optional[int]]:
        return self.model_dump()


class GoogleFrontDoor(BaseModel):
    """The provider's primary Google Business Profile — the real, fetched read."""
    rating: Optional[float] = None
    count: Optional[int] = None
    responds_to_reviews: Optional[bool] = None  # not exposed by Places API → usually None
    recency: Optional[str] = None
    verified: bool = False
    reason: Optional[str] = None                 # populated when not verified


class GoogleFootprint(BaseModel):
    front_door: GoogleFrontDoor = Field(default_factory=GoogleFrontDoor)
    listings_estimate: str = ""   # breadth, sampled (e.g. "1 brand + ~6 locations")
    rating_range: str = ""        # e.g. "3.2–4.6 across location listings"
    consistency: str = ""         # "unified/claimed" vs "fragmented/unclaimed"
    gap_note: str = ""            # one-line footprint opening vs. clinical quality


class ThirdPartyAggregate(BaseModel):
    """The 'Google vs. the rest of the web' contrast (Healthgrades/Vitals/WebMD)."""
    rating: Optional[float] = None
    sources: str = "Healthgrades, Vitals, WebMD"
    note: str = ""


class RankedProvider(BaseModel):
    rank: int
    name: str
    affiliation_type: AffiliationType = AffiliationType.unknown
    size_category: SizeCategory = SizeCategory.unknown
    physician_count: Optional[str] = None  # e.g. "12", "~20", "3–5", or None if unknown
    overall_rating: str = ""               # e.g. "A" or "4.2/5 stars"
    # AI Visibility Score (computed deterministically from tier_scores by profile)
    ai_visibility_score: Optional[int] = None
    weighting_profile: Optional[str] = None  # "procedural" | "relationship"
    tier_scores: TierScores = Field(default_factory=TierScores)
    google_footprint: GoogleFootprint = Field(default_factory=GoogleFootprint)
    third_party_aggregate: ThirdPartyAggregate = Field(default_factory=ThirdPartyAggregate)
    disqualifiers: list[str] = Field(default_factory=list)
    key_strengths: list[str] = Field(default_factory=list)
    notable_weaknesses: list[str] = Field(default_factory=list)
    best_suited_for: str = ""
    recommendation_summary: str = ""
    consolidated_locations: list[ConsolidatedLocation] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Structured result from a Claude-powered market analysis."""
    run_id: str
    location: str                          # e.g. "Mobile, Alabama"
    specialty: Optional[str] = None        # None → broad hospital analysis
    aggregate: bool = False                # whether parent/child entities were consolidated
    generated_at: date
    weighting_profile: Optional[str] = None  # the profile used for the whole run
    market_overview: str = ""              # 2–3 paragraph landscape narrative
    ai_visibility_verdict: str = ""        # neutral analyst read on the market's AI visibility
    coverage_note: str = ""                # "covered N of M registry facilities"
    top_recommendation: str = ""
    practical_advice: list[str] = Field(default_factory=list)
    disclaimer: str = ""
    rankings: list[RankedProvider] = Field(default_factory=list)
    report_markdown: str = ""              # full narrative report text
    pdf_path: Optional[str] = None
    md_path: Optional[str] = None
