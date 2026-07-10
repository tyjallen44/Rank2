"""Pydantic models for System Composite (Tier 3) data structures."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OwnershipTier(str, Enum):
    owned        = "OWNED"
    controlled   = "CONTROLLED"
    affiliated   = "AFFILIATED"
    in_transition = "IN-TRANSITION"


class FootprintClass(str, Enum):
    facility_centric   = "FACILITY-CENTRIC"
    balanced           = "BALANCED"
    ambulatory_forward = "AMBULATORY-FORWARD"


class NetworkResolution(str, Enum):
    correct  = "correct"
    partial  = "partial"
    confused = "confused"
    unknown  = "unknown"


class NetworkEntityDraft(BaseModel):
    """Candidate entity as returned by Claude discovery (pre-confirmation)."""
    name: str
    entity_type: str                               # "practice" | "hospital"
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    proposed_tier: OwnershipTier = OwnershipTier.owned
    ownership_evidence_source: str = ""
    ownership_verified: bool = False
    fte_count: Optional[int] = None
    encounter_volume_share: Optional[float] = None
    strategic_multiplier: float = 1.0
    strategic_multiplier_rationale: Optional[str] = None
    transition_close_date: Optional[str] = None    # ISO date string
    linked_run_id: Optional[str] = None            # matched existing Tier 1/2 run


class NetworkEntity(BaseModel):
    """Confirmed entity stored in the ownership registry."""
    id: str
    registry_id: str
    name: str
    entity_type: str
    city: Optional[str] = None
    state: Optional[str] = None
    inclusion_tier: OwnershipTier
    ownership_evidence_source: str = ""
    ownership_verified: bool = False
    inclusion_weight: float = 1.0      # final w_i (tier discount + strategic multiplier, renorm'd)
    fte_count: Optional[int] = None
    encounter_volume_share: Optional[float] = None
    strategic_multiplier: float = 1.0
    strategic_multiplier_rationale: Optional[str] = None
    transition_close_date: Optional[str] = None
    linked_run_id: Optional[str] = None


class NetworkRegistry(BaseModel):
    """Confirmed ownership registry for one market / composite run."""
    id: str
    anchor_run_id: Optional[str] = None
    system_name: str
    market_cbsa: Optional[str] = None
    radius_miles: int = 50
    attested_at: str                   # ISO datetime string
    re_attest_due: str
    entities: list[NetworkEntity] = Field(default_factory=list)


class NetworkBatteryRun(BaseModel):
    """One coded prompt run from the N1–N4 network battery."""
    id: str
    registry_id: str
    composite_run_id: str
    prompt_category: str               # N1 | N2 | N3 | N4
    prompt_number: int
    prompt_text: str
    assistant: str
    retrieval_mode: str                # "on" | "off"
    response_text: str
    network_resolution: NetworkResolution
    run_date: str


class ModifierEntry(BaseModel):
    """One entry in the composite modifier ledger."""
    modifier: str
    effect: str
    tier_of_origin: str                # "1" | "2" | "3"
    points: float


class CompositeResult(BaseModel):
    """Final output of a System Composite analysis."""
    id: str
    registry_id: str
    anchor_run_id: Optional[str] = None
    system_name: str = ""
    hospital_score: float
    network_score: float
    attributed_network_score: float
    sar: float                         # System Attribution Rate 0–1
    footprint_class: FootprintClass
    w_h: float
    w_n: float
    continuum_coherence: float
    continuum_bonus: float
    composite_score: float
    composite_grade: str
    merged_entity_delta: float         # composite_score − hospital_score
    network_capture_rate: Optional[float] = None
    leakage_index: float = 0.0
    score_ceiling_applied: bool = False
    score_ceiling_reason: Optional[str] = None
    small_network_refused: bool = False
    proxy_weighted: bool = False
    cross_tier_flag: bool = False      # True if cross-tier runs > 45 days apart
    modifier_ledger: list[ModifierEntry] = Field(default_factory=list)
    per_assistant_sar: dict = Field(default_factory=dict)
    orphan_entity_ids: list[str] = Field(default_factory=list)
    rubric_version_hospital: str = "hospital-v1.0"
    rubric_version_practice: str = "practice-v1.0"
    rubric_version_composite: str = "composite-v1.0"
    oldest_input_date: Optional[str] = None
    composite_expires_at: Optional[str] = None
    composite_mode: str = "hospitals_and_practices"
    network_battery_runs: list[NetworkBatteryRun] = Field(default_factory=list)
    entities: list[NetworkEntity] = Field(default_factory=list)
    generated_at: Optional[str] = None
    report_narrative: str = ""
    pdf_path: Optional[str] = None
    md_path: Optional[str] = None
