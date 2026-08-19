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
    google_rating: Optional[float] = None
    google_review_count: Optional[int] = None
    address: Optional[str] = None


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


class SystemAggregate(BaseModel):
    """Review-count-weighted Google rating across all of a system's locations."""
    rating: Optional[float] = None
    total_reviews: int = 0
    location_count: int = 0
    confidence: str = ""          # "registry" (NPPES-enumerated) | "sample"
    capped: bool = False

    @property
    def available(self) -> bool:
        return self.rating is not None and self.location_count > 0


class GoogleFootprint(BaseModel):
    front_door: GoogleFrontDoor = Field(default_factory=GoogleFrontDoor)
    listings_estimate: str = ""   # breadth, sampled (e.g. "1 brand + ~6 locations")
    rating_range: str = ""        # e.g. "3.2–4.6 across location listings"
    consistency: str = ""         # "unified/claimed" vs "fragmented/unclaimed"
    gap_note: str = ""            # one-line footprint opening vs. clinical quality
    # Authoritative system-wide weighted reputation (multi-location systems only)
    system_aggregate: SystemAggregate = Field(default_factory=SystemAggregate)


class ThirdPartyAggregate(BaseModel):
    """The 'Google vs. the rest of the web' contrast (Healthgrades/Vitals/WebMD)."""
    rating: Optional[float] = None
    sources: str = "Healthgrades, Vitals, WebMD"
    note: str = ""


class UsNewsRanking(BaseModel):
    """A single U.S. News & World Report recognition for a provider."""
    category: str                                # e.g. "Orthopedics", "Cardiology & Heart Surgery"
    rank: Optional[int] = None                   # specific rank number; None if only high-performing
    recognition_type: str = "nationally_ranked"  # "nationally_ranked" | "high_performing"


class RankedProvider(BaseModel):
    rank: int
    name: str
    website_url: Optional[str] = None
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
    patient_voice_summary: str = ""
    leapfrog_grade: Optional[str] = None
    accreditations: list[str] = Field(default_factory=list)
    cms_quality_highlights: str = ""
    cms_star_rating: Optional[int] = None          # CMS Overall Hospital Quality Star Rating 1–5
    us_news_rankings: list[UsNewsRanking] = Field(default_factory=list)
    ai_says: str = ""                              # how AI assistants currently describe this provider
    trauma_level: Optional[str] = None            # "Level I" / "Level II" / "Level III"
    teaching_status: Optional[str] = None         # "major" / "minor" / "not_teaching"
    # "hospital" (default) or "practice" — gates hospital-only signal injection in PDF
    report_type: str = "hospital"
    # Practice Edition derived metrics (None for hospital runs)
    entity_resolution_pct: Optional[float] = None   # % of runs resolving the correct practice
    linkage_integrity_pct: Optional[float] = None   # % of physician runs correctly linked
    physician_capture_rate: Optional[float] = None  # % of physician-first queries capturing any physician
    key_person_flag: bool = False                   # solo/two-physician practice flag
    score_ceiling_applied: bool = False             # True if the ≤74 ceiling was applied
    score_ceiling_reason: Optional[str] = None      # which triggers fired the ceiling
    # Simplified Patient Pulse: True for the prospect/target entity (rendered in full);
    # every other entity is obscured in that report.
    is_target: bool = False


class ImprovementSection(BaseModel):
    """A labeled group of improvement action items within the AI Visibility Assessment."""
    title: str
    description: str
    items: list[str] = Field(default_factory=list)


class ComparisonSummary(BaseModel):
    """Structured comparison between two individual-report entities."""
    headline: str = ""                          # one-line synthesis
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    verdict: str = ""                           # 2–3 paragraph analyst narrative


class BatteryPromptResult(BaseModel):
    """Per-prompt battery run record for demo eligibility and variance tracking."""
    prompt_id: str
    prompt_text: str
    run_count: int
    outcomes: list[str]          # coded: "recommended"|"mentioned"|"absent"|"competitor_named"|"error"
    dominant_outcome: str
    dominant_pct: float          # fraction of runs with dominant_outcome


class FqhcPillarScores(BaseModel):
    """FQHC Community Health Edition pillar sub-scores (0–100 each).

    mqcr_score and multilingual_score are None in Round 1 (no battery runs logged).
    The composite is computed by fqhc_scoring.composite() from these fields.
    """
    mqcr_score: Optional[int] = None                # Pillar 1a: Mission Query Capture Rate
    multilingual_score: Optional[int] = None        # Pillar 1b: Multilingual capture
    service_adjacent_score: Optional[int] = None    # Pillar 1c: Service-adjacent findability
    eligibility_cost_accuracy: Optional[int] = None # Pillar 2
    site_service_completeness: Optional[int] = None # Pillar 3
    experience_reputation: Optional[int] = None     # Pillar 4
    institutional_signals: Optional[int] = None     # Pillar 5

    def as_dict(self) -> dict:
        return self.model_dump()


class AnalysisResult(BaseModel):
    """Structured result from a Claude-powered market analysis."""
    run_id: str
    location: str                          # e.g. "Mobile, Alabama"
    specialty: Optional[str] = None        # None → broad hospital analysis
    aggregate: bool = False                # whether parent/child entities were consolidated
    patient_perspective: bool = False      # flat ranking by score, no entity-type grouping
    teaser_report: bool = False            # summary-only cards with CTA; implies patient_perspective
    # Simplified Patient Pulse (BD enticement): compact 2-block cards ranked by AI
    # Visibility Score; the target entity renders in full, every competitor is
    # obscured. Implies patient_perspective.
    simplified: bool = False
    # True (Enticement): highlight the target, obscure every competitor.
    # False (Market Summary): show all providers clearly, no target, no blur.
    obscure_competitors: bool = True
    target_entity: Optional[str] = None    # name of the prospect/target entity (enticement mode)
    individual_report: bool = False        # single-entity deep-dive report
    entity_name: Optional[str] = None     # named entity for individual_report mode
    report_title: Optional[str] = None    # display name override (PDF title); defaults to entity_name
    zip_code: Optional[str] = None         # set when search was by ZIP code
    radius_miles: Optional[int] = None     # set when search was by ZIP code
    generated_at: date
    weighting_profile: Optional[str] = None  # the profile used for the whole run
    entity_type: Optional[str] = None        # "hospital" | "practice" — None means hospital
    rubric_version: Optional[str] = None     # e.g. "practice-v1.0"
    practice_profile: Optional[str] = None  # e.g. "practice_procedural"
    practice_composite_rows: list[dict] = Field(default_factory=list)   # practice reputation data
    physician_composite_rows: list[dict] = Field(default_factory=list)  # physician reputation data (flat)
    market_overview: str = ""              # 2–3 paragraph landscape narrative
    ai_visibility_verdict: str = ""        # neutral analyst read on the market's AI visibility
    coverage_note: str = ""                # "covered N of M registry facilities"
    top_recommendation: str = ""
    practical_advice: list[str] = Field(default_factory=list)
    improvement_sections: list[ImprovementSection] = Field(default_factory=list)
    disclaimer: str = ""
    rankings: list[RankedProvider] = Field(default_factory=list)
    report_markdown: str = ""              # full narrative report text
    pdf_path: Optional[str] = None
    md_path: Optional[str] = None
    briefing_variant: Optional[str] = None          # "sales" | "cs" | None
    briefing_pdf_path: Optional[str] = None         # path to companion briefing PDF
    briefing_skipped_reason: Optional[str] = None   # set when briefing was requested but failed
    battery_results: Optional[dict[str, BatteryPromptResult]] = None  # prompt_id → result
    # ── FQHC Community Health Edition fields (None for hospital/practice runs) ──
    fqhc_intake: Optional[dict] = None              # client-attested intake facts
    fqhc_pillar_scores: Optional[FqhcPillarScores] = None  # 7-field sub-score record
    fqhc_fact_audit: list[dict] = Field(default_factory=list)  # Pillar 2 audit rows
    fqhc_missed_queries: list[dict] = Field(default_factory=list)  # Missed Queries exhibit
    fqhc_mqcr: Optional[float] = None               # MQCR (0.0–1.0); None until battery run


# ── Network Pulse — multi-state hospital network models ──────────────────────

class NetworkFacility(BaseModel):
    name: str
    city: str
    state: str
    beds: Optional[int] = None
    ai_visibility_score: Optional[int] = None   # 0–100
    grade: Optional[str] = None
    surfaced_for_local: Optional[bool] = None   # found when patients search "[city] hospital"
    attributed_to_network: Optional[bool] = None  # correctly linked to parent network
    key_gap: Optional[str] = None               # single most important gap
    google_rating: Optional[float] = None       # verified Google rating (1–5)
    google_review_count: Optional[int] = None   # number of Google reviews
    cms_star_rating: Optional[int] = None       # CMS Overall Hospital Quality Star Rating (1–5)
    leapfrog_grade: Optional[str] = None        # Leapfrog Hospital Safety Grade (A/B/C/D/F)


class NetworkResult(BaseModel):
    run_id: str
    network_name: str
    network_canonical_name: Optional[str] = None
    hq_location: Optional[str] = None           # "Charlotte, NC"
    source_url: Optional[str] = None            # locations page URL used for roster extraction
    facility_type: str = "hospital"             # "hospital"|"asc"|"urgent_care"|"imaging"|"behavioral_health"|"other"
    total_hospitals: int = 0                    # count of assessed facilities (name kept for DB compat)
    states_covered: list[str] = Field(default_factory=list)
    generated_at: date
    # Scores (0–100 each)
    ai_visibility_score: Optional[int] = None   # overall composite
    brand_visibility_score: Optional[int] = None  # 40% weight
    market_coverage_score: Optional[int] = None   # 35% weight
    information_accuracy_score: Optional[int] = None  # 25% weight
    grade: Optional[str] = None                 # quartile code ("Q1"…) — synced with Market rubric
    grade_band: Optional[str] = None            # "Top Quartile" etc.
    # Four-pillar entity score (shared canonical rubric — synced with the Market report)
    tier_scores: Optional[dict] = None          # {clinical_outcomes_safety, credentials_recognition, patient_experience_reviews, access_fit}
    weighting_profile: Optional[str] = None     # drives pillar labels in the PDF
    ai_says: str = ""                           # "What AI assistants currently see" for the network entity
    # Narrative (written for C-suite)
    executive_summary: str = ""
    brand_visibility_narrative: str = ""
    market_coverage_narrative: str = ""
    information_accuracy_narrative: str = ""
    strategic_recommendations: list[str] = Field(default_factory=list)
    top_markets: list[str] = Field(default_factory=list)   # best-performing markets
    gap_markets: list[str] = Field(default_factory=list)   # worst-performing markets
    # Facility-level data
    facilities: list[NetworkFacility] = Field(default_factory=list)
    # Meta
    pdf_path: Optional[str] = None
    teaser_pdf_path: Optional[str] = None
    entity_type: str = "hospital_network"
