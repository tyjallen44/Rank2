"""System Composite (Tier 3) analysis pipeline.

Consumes finished Tier 1 (hospital) and Tier 2 (practice) outputs.
Never re-scores underlying signals — the composite is a pure overlay.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import anthropic

from .analyzer import _get_client, _MODEL, _clean
from .composite_config import COMPOSITE_CONFIG
from .composite_models import (
    CompositeResult,
    FootprintClass,
    ModifierEntry,
    NetworkBatteryRun,
    NetworkEntity,
    NetworkEntityDraft,
    NetworkRegistry,
    NetworkResolution,
    OwnershipTier,
)
from .composite_scoring import (
    check_small_network,
    compute_composite,
    compute_leakage_index,
    compute_orphan_volume_share,
    compute_sar,
    network_score,
    points_recoverable,
)
from .db import get_connection, init_db

_RUBRIC_VERSION_COMPOSITE = "composite-v1.0"

# ─────────────────────────────────────────────────────────────────────────────
# Network discovery
# ─────────────────────────────────────────────────────────────────────────────

_DISCOVER_TOOL = {
    "name": "submit_network_roster",
    "description": (
        "Submit the discovered network entity roster for this health system. "
        "Include all owned practices, employed physician groups, owned clinics, "
        "and sibling hospitals. Exclude purely affiliated or referring entities."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "system_name": {
                "type": "string",
                "description": "Canonical name of the health system.",
            },
            "market_cbsa": {
                "type": ["string", "null"],
                "description": "CBSA/metro area name for this market.",
            },
            "entities": {
                "type": "array",
                "description": "Discovered network entities (practices and hospitals).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "entity_type": {
                            "type": "string",
                            "enum": ["practice", "hospital"],
                        },
                        "city": {"type": ["string", "null"]},
                        "state": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                        "proposed_tier": {
                            "type": "string",
                            "enum": ["OWNED", "CONTROLLED", "AFFILIATED", "IN-TRANSITION"],
                        },
                        "ownership_evidence_source": {
                            "type": "string",
                            "description": (
                                "Source of ownership evidence: 'system_website', "
                                "'nppes_record', 'news_article', 'public_filing', 'inferred'."
                            ),
                        },
                        "fte_estimate": {"type": ["integer", "null"]},
                        "transition_close_date": {
                            "type": ["string", "null"],
                            "description": "ISO date if IN-TRANSITION, else null.",
                        },
                    },
                    "required": [
                        "name", "entity_type", "proposed_tier",
                        "ownership_evidence_source",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["system_name", "entities"],
        "additionalProperties": False,
    },
}


def discover_network(
    entity_name: str,
    city: str,
    state: str,
    on_event: Optional[Callable] = None,
) -> tuple[str, Optional[str], list[NetworkEntityDraft]]:
    """
    Use Claude to discover what practices and hospitals are part of this system.

    Returns (system_name, market_cbsa, list_of_drafts).
    The caller presents results to the user for confirmation before any run starts.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    client = _get_client()
    prompt = (
        f"You are researching the health system '{entity_name}' based in {city}, {state}.\n\n"
        "Using your knowledge of health system ownership structures, identify all entities "
        "that this system OWNS or EMPLOYS as of your training cutoff:\n"
        "- Owned physician practices and specialty clinics\n"
        "- Employed physician groups (medical group, multispecialty group)\n"
        "- Ambulatory surgery centers owned by the system\n"
        "- Sibling hospitals under the same parent organization\n"
        "- Urgent care clinics operating under the system brand\n\n"
        "For each entity specify: name, type (practice/hospital), city/state if known, "
        "ownership tier (OWNED=wholly owned, CONTROLLED=majority JV, "
        "AFFILIATED=referral-only, IN-TRANSITION=recently acquired), "
        "and what source indicates ownership.\n\n"
        "Focus on the local market (within ~50 miles of the anchor location). "
        "Do NOT include purely affiliated community physicians or independent referral partners.\n\n"
        "Call submit_network_roster with your findings."
    )

    emit({"type": "text", "text": f"Discovering network for {entity_name}…"})

    with client.messages.stream(
        model=_MODEL,
        max_tokens=4000,
        tools=[_DISCOVER_TOOL],
        tool_choice={"type": "tool", "name": "submit_network_roster"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    roster_data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_network_roster":
            roster_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    system_name = roster_data.get("system_name") or entity_name
    market_cbsa = roster_data.get("market_cbsa")
    raw_entities = roster_data.get("entities") or []

    drafts: list[NetworkEntityDraft] = []
    for e in raw_entities:
        if not e.get("name"):
            continue
        try:
            tier = OwnershipTier(e.get("proposed_tier", "OWNED"))
        except ValueError:
            tier = OwnershipTier.owned
        drafts.append(NetworkEntityDraft(
            name=_clean(e["name"]),
            entity_type=e.get("entity_type", "practice"),
            city=e.get("city") or city,
            state=e.get("state") or state,
            address=e.get("address"),
            proposed_tier=tier,
            ownership_evidence_source=e.get("ownership_evidence_source") or "inferred",
            fte_count=e.get("fte_estimate"),
            transition_close_date=e.get("transition_close_date"),
        ))

    emit({"type": "text", "text": f"Found {len(drafts)} candidate network entities."})
    return system_name, market_cbsa, drafts


# ─────────────────────────────────────────────────────────────────────────────
# Registry persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_registry(
    anchor_run_id: str,
    system_name: str,
    market_cbsa: Optional[str],
    radius_miles: int,
    confirmed_entities: list[dict],   # dicts from the API confirmation payload
) -> NetworkRegistry:
    """
    Persist a confirmed network registry and its entities.

    Computes final inclusion_weight for each entity (tier discount × strategic
    multiplier, renormalized across scoreable entities).
    """
    con = get_connection()
    registry_id = str(uuid.uuid4())
    now = datetime.utcnow()
    reattest_due = now + timedelta(days=COMPOSITE_CONFIG["registry_reattest_days"])

    con.execute(
        """INSERT INTO network_registries
           (id, anchor_run_id, system_name, market_cbsa, radius_miles,
            attested_at, re_attest_due, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [registry_id, anchor_run_id, system_name, market_cbsa, radius_miles,
         now.isoformat(), reattest_due.isoformat(), now.isoformat()],
    )

    tier_weights = COMPOSITE_CONFIG["inclusion_weights"]
    sm_cfg       = COMPOSITE_CONFIG["strategic_multiplier"]

    # Compute raw weights (before renormalization)
    entities_data: list[dict] = []
    for e in confirmed_entities:
        tier_str = e.get("inclusion_tier", "OWNED")
        try:
            tier = OwnershipTier(tier_str)
        except ValueError:
            tier = OwnershipTier.owned

        base_w  = tier_weights.get(tier.value, 1.0)
        enc_vol = e.get("encounter_volume_share")   # client-supplied or None
        fte     = e.get("fte_count")

        # Strategic multiplier
        mult = float(e.get("strategic_multiplier") or 1.0)
        mult = max(1.0, min(sm_cfg["max_value"], mult))

        # Raw weight: prefer encounter_volume_share, fallback fte, fallback 1.0
        if enc_vol is not None:
            raw = float(enc_vol) * base_w * mult
        elif fte:
            raw = float(fte) * base_w * mult
        else:
            raw = 1.0 * base_w * mult

        entities_data.append({
            "id": str(uuid.uuid4()),
            "registry_id": registry_id,
            "name": _clean(e.get("name", "")),
            "entity_type": e.get("entity_type", "practice"),
            "city": e.get("city"),
            "state": e.get("state"),
            "inclusion_tier": tier.value,
            "ownership_evidence_source": e.get("ownership_evidence_source", "client_attested"),
            "ownership_verified": bool(e.get("ownership_verified", False)),
            "raw_weight": raw,
            "fte_count": fte,
            "encounter_volume_share": enc_vol,
            "strategic_multiplier": mult,
            "strategic_multiplier_rationale": e.get("strategic_multiplier_rationale"),
            "transition_close_date": e.get("transition_close_date"),
            "linked_run_id": e.get("linked_run_id"),
        })

    # Renormalize among scoreable entities (not AFFILIATED)
    scoreable_sum = sum(
        d["raw_weight"] for d in entities_data
        if d["inclusion_tier"] != "AFFILIATED"
    )
    if scoreable_sum == 0:
        scoreable_sum = 1.0

    network_entities: list[NetworkEntity] = []
    for d in entities_data:
        if d["inclusion_tier"] == "AFFILIATED":
            final_w = 0.0
        else:
            final_w = d["raw_weight"] / scoreable_sum

        con.execute(
            """INSERT INTO network_entities
               (id, registry_id, name, entity_type, city, state,
                inclusion_tier, ownership_evidence_source, ownership_verified,
                inclusion_weight, fte_count, encounter_volume_share,
                strategic_multiplier, strategic_multiplier_rationale,
                transition_close_date, linked_run_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                d["id"], registry_id, d["name"], d["entity_type"],
                d["city"], d["state"], d["inclusion_tier"],
                d["ownership_evidence_source"], d["ownership_verified"],
                final_w, d["fte_count"], d["encounter_volume_share"],
                d["strategic_multiplier"], d["strategic_multiplier_rationale"],
                d["transition_close_date"], d["linked_run_id"],
                now.isoformat(),
            ],
        )
        network_entities.append(NetworkEntity(
            id=d["id"],
            registry_id=registry_id,
            name=d["name"],
            entity_type=d["entity_type"],
            city=d["city"],
            state=d["state"],
            inclusion_tier=OwnershipTier(d["inclusion_tier"]),
            ownership_evidence_source=d["ownership_evidence_source"],
            ownership_verified=d["ownership_verified"],
            inclusion_weight=final_w,
            fte_count=d["fte_count"],
            encounter_volume_share=d["encounter_volume_share"],
            strategic_multiplier=d["strategic_multiplier"],
            strategic_multiplier_rationale=d["strategic_multiplier_rationale"],
            transition_close_date=d["transition_close_date"],
            linked_run_id=d["linked_run_id"],
        ))

    con.close()
    return NetworkRegistry(
        id=registry_id,
        anchor_run_id=anchor_run_id,
        system_name=system_name,
        market_cbsa=market_cbsa,
        radius_miles=radius_miles,
        attested_at=now.isoformat(),
        re_attest_due=reattest_due.isoformat(),
        entities=network_entities,
    )


def load_registry(registry_id: str) -> Optional[NetworkRegistry]:
    """Load a registry and its entities from the database."""
    con = get_connection()
    row = con.execute(
        "SELECT * FROM network_registries WHERE id = ?", [registry_id]
    ).fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    reg_dict = dict(zip(cols, row))

    entity_rows = con.execute(
        "SELECT * FROM network_entities WHERE registry_id = ? ORDER BY inclusion_weight DESC",
        [registry_id],
    ).fetchall()
    e_cols = [d[0] for d in con.description]
    con.close()

    entities = []
    for er in entity_rows:
        ed = dict(zip(e_cols, er))
        entities.append(NetworkEntity(
            id=ed["id"],
            registry_id=ed["registry_id"],
            name=ed["name"],
            entity_type=ed["entity_type"],
            city=ed.get("city"),
            state=ed.get("state"),
            inclusion_tier=OwnershipTier(ed["inclusion_tier"]),
            ownership_evidence_source=ed.get("ownership_evidence_source", ""),
            ownership_verified=bool(ed.get("ownership_verified", False)),
            inclusion_weight=float(ed.get("inclusion_weight") or 0.0),
            fte_count=ed.get("fte_count"),
            encounter_volume_share=ed.get("encounter_volume_share"),
            strategic_multiplier=float(ed.get("strategic_multiplier") or 1.0),
            strategic_multiplier_rationale=ed.get("strategic_multiplier_rationale"),
            transition_close_date=ed.get("transition_close_date"),
            linked_run_id=ed.get("linked_run_id"),
        ))

    return NetworkRegistry(
        id=reg_dict["id"],
        anchor_run_id=reg_dict["anchor_run_id"],
        system_name=reg_dict["system_name"],
        market_cbsa=reg_dict.get("market_cbsa"),
        radius_miles=reg_dict.get("radius_miles", 50),
        attested_at=str(reg_dict.get("attested_at", "")),
        re_attest_due=str(reg_dict.get("re_attest_due", "")),
        entities=entities,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Staleness checks
# ─────────────────────────────────────────────────────────────────────────────

def _run_age_days(run_id: str) -> Optional[float]:
    """Return how old this run is in days, or None if not found."""
    con = get_connection()
    row = con.execute(
        "SELECT generated_at FROM analysis_runs WHERE run_id = ?", [run_id]
    ).fetchone()
    con.close()
    if not row or not row[0]:
        return None
    try:
        run_date = row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        return (date.today() - run_date).days
    except Exception:
        return None


def _check_staleness(entities: list[NetworkEntity]) -> tuple[bool, bool]:
    """
    Returns (any_stale, cross_tier_flag).
    any_stale: at least one linked run is > 90 days old.
    cross_tier_flag: runs are >45 days apart from each other.
    """
    ages = []
    for e in entities:
        if e.linked_run_id:
            age = _run_age_days(e.linked_run_id)
            if age is not None:
                ages.append(age)

    threshold = COMPOSITE_CONFIG["staleness_days"]
    cross_flag_days = COMPOSITE_CONFIG["cross_tier_flag_days"]

    any_stale = any(a > threshold for a in ages)
    cross_flag = (max(ages) - min(ages)) > cross_flag_days if len(ages) >= 2 else False
    return any_stale, cross_flag


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 linkage data retrieval
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_linkage_data(
    entities: list[NetworkEntity],
    integration_grace_months: int = 12,
) -> tuple[dict, float]:
    """
    Fetch linkage_integrity_pct and physician_capture_rate from ranked_providers
    for each entity's linked_run_id.

    Returns:
      - linkage_data: {entity_id: linkage_integrity_pct (0–100)} — None for
        IN-TRANSITION entities inside the grace window (excluded from SAR)
      - weighted_capture_rate: volume-weighted avg physician_capture_rate across practices
    """
    con = get_connection()
    linkage_data: dict = {}
    capture_rates: list[tuple[float, float]] = []   # (rate, weight)

    cutoff_months = integration_grace_months
    for e in entities:
        if e.inclusion_tier == OwnershipTier.affiliated:
            continue
        if not e.linked_run_id:
            linkage_data[e.id] = None
            continue

        row = con.execute(
            "SELECT linkage_integrity_pct, physician_capture_rate "
            "FROM ranked_providers WHERE run_id = ? AND rank = 1",
            [e.linked_run_id],
        ).fetchone()

        if not row:
            linkage_data[e.id] = None
            continue

        lip, pcr = row

        # Integration grace: IN-TRANSITION within 12 months → exclude from SAR
        if e.inclusion_tier == OwnershipTier.in_transition and e.transition_close_date:
            try:
                close = date.fromisoformat(e.transition_close_date)
                months_elapsed = (date.today() - close).days / 30.44
                if months_elapsed < cutoff_months:
                    linkage_data[e.id] = None   # excluded from tier-2 SAR component
                    continue
            except Exception:
                pass

        linkage_data[e.id] = float(lip) if lip is not None else None

        if pcr is not None and e.entity_type == "practice":
            capture_rates.append((float(pcr), e.inclusion_weight))

    con.close()

    total_w = sum(w for _, w in capture_rates)
    wcr = (
        sum(r * w for r, w in capture_rates) / total_w
        if total_w > 0 else 0.0
    )
    # Convert from 0–100 scale to 0–1
    wcr = wcr / 100.0 if wcr > 1.0 else wcr
    return linkage_data, wcr


# ─────────────────────────────────────────────────────────────────────────────
# Hospital score retrieval
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_hospital_score(run_id: str) -> Optional[float]:
    """Return the AI Visibility Score for the anchor run's rank-1 provider."""
    con = get_connection()
    row = con.execute(
        "SELECT ai_visibility_score FROM ranked_providers WHERE run_id = ? AND rank = 1",
        [run_id],
    ).fetchone()
    con.close()
    return float(row[0]) if row and row[0] is not None else None


def _fetch_practice_scores(entities: list[NetworkEntity]) -> dict:
    """
    Return {entity_id: ai_visibility_score} for entities that have a linked run.
    Entities without a run get None (caller decides how to handle missing scores).
    """
    con = get_connection()
    scores: dict = {}
    for e in entities:
        if not e.linked_run_id or e.inclusion_tier == OwnershipTier.affiliated:
            scores[e.id] = None
            continue
        row = con.execute(
            "SELECT ai_visibility_score FROM ranked_providers WHERE run_id = ? AND rank = 1",
            [e.linked_run_id],
        ).fetchone()
        scores[e.id] = float(row[0]) if row and row[0] is not None else None
    con.close()
    return scores


# ─────────────────────────────────────────────────────────────────────────────
# Network battery (N1–N4)
# ─────────────────────────────────────────────────────────────────────────────

_BATTERY_TOOL = {
    "name": "code_network_resolution",
    "description": (
        "After simulating how you respond to the given query about the health system, "
        "code the network_resolution dimension and provide your simulated response."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "simulated_response": {
                "type": "string",
                "description": "What you would actually say to a patient asking this question.",
            },
            "network_resolution": {
                "type": "string",
                "enum": ["correct", "partial", "confused", "unknown"],
                "description": (
                    "correct=system and roster accurately described; "
                    "partial=some entities right, roster incomplete; "
                    "confused=wrong entities attributed or misidentified; "
                    "unknown=insufficient data to answer."
                ),
            },
        },
        "required": ["simulated_response", "network_resolution"],
        "additionalProperties": False,
    },
}


def run_network_battery(
    registry: NetworkRegistry,
    on_event: Optional[Callable] = None,
) -> list[NetworkBatteryRun]:
    """
    Run the N1–N4 network battery prompts through Claude.

    Returns a list of NetworkBatteryRun objects with network_resolution coded.
    For MVP, runs each prompt once with retrieval simulated; marks assistant='claude'.
    """
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    client = _get_client()
    composite_run_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    system_name = registry.system_name
    city = next(
        (e.city for e in registry.entities if e.city),
        "the market"
    )
    state = next(
        (e.state for e in registry.entities if e.state),
        ""
    )
    location = f"{city}, {state}" if state else city

    # Pick one practice and one specialty for parameterized prompts
    practices = [e for e in registry.entities if e.entity_type == "practice"]
    sample_practice = practices[0].name if practices else f"{system_name} Medical Group"

    prompts = [
        ("N1", 1, f"What clinics and practices are part of {system_name}?"),
        ("N1", 2, f"Does {system_name} have primary care or specialty offices near {location}?"),
        ("N1", 3, f"Is {sample_practice} part of {system_name}?"),
        ("N2", 4, f"I want to stay within {system_name} — find me a primary care doctor."),
        ("N2", 5, f"Does {system_name} have a doctor who can see me this week for a routine visit?"),
        ("N3", 6, f"If I see a {system_name} primary care doctor and need surgery, where would I be sent?"),
        ("N3", 7, f"Can {system_name} handle everything from diagnosis to treatment near {location}?"),
        ("N4", 8, f"Is {sample_practice} independently owned or is it part of {system_name}?"),
        ("N4", 9, f"Which hospital do doctors at {sample_practice} admit to?"),
    ]

    runs: list[NetworkBatteryRun] = []
    emit({"type": "text", "text": f"Running network battery ({len(prompts)} prompts)…"})

    for category, num, prompt_text in prompts:
        run_prompt = (
            f"A patient asks you: '{prompt_text}'\n\n"
            f"First, simulate how you would actually respond to this patient based on your "
            f"knowledge of {system_name} in {location}. Then code the network_resolution "
            f"dimension: 'correct' if you accurately described the system and its network, "
            f"'partial' if your roster was incomplete, 'confused' if you misidentified entities, "
            f"'unknown' if you lacked enough information to answer specifically.\n\n"
            f"Call code_network_resolution with your simulated response and the resolution code."
        )

        try:
            with client.messages.stream(
                model=_MODEL,
                max_tokens=1000,
                tools=[_BATTERY_TOOL],
                tool_choice={"type": "tool", "name": "code_network_resolution"},
                messages=[{"role": "user", "content": run_prompt}],
            ) as stream:
                response = stream.get_final_message()

            coded: dict = {}
            for block in response.content:
                if block.type == "tool_use" and block.name == "code_network_resolution":
                    coded = block.input if isinstance(block.input, dict) else json.loads(block.input)
                    break

            resolution_str = coded.get("network_resolution", "unknown")
            try:
                resolution = NetworkResolution(resolution_str)
            except ValueError:
                resolution = NetworkResolution.unknown

            run = NetworkBatteryRun(
                id=str(uuid.uuid4()),
                registry_id=registry.id,
                composite_run_id=composite_run_id,
                prompt_category=category,
                prompt_number=num,
                prompt_text=prompt_text,
                assistant="claude",
                retrieval_mode="on",
                response_text=coded.get("simulated_response", ""),
                network_resolution=resolution,
                run_date=now,
            )
            runs.append(run)
        except Exception as exc:
            runs.append(NetworkBatteryRun(
                id=str(uuid.uuid4()),
                registry_id=registry.id,
                composite_run_id=composite_run_id,
                prompt_category=category,
                prompt_number=num,
                prompt_text=prompt_text,
                assistant="claude",
                retrieval_mode="on",
                response_text=f"[error: {exc}]",
                network_resolution=NetworkResolution.unknown,
                run_date=now,
            ))

    return runs


def _battery_resolution_score(runs: list[NetworkBatteryRun]) -> tuple[float, float]:
    """
    Returns (battery_sar_component, continuum_coherence).

    battery_sar_component: normalized 0–1 from N1/N4 prompts
    continuum_coherence: % of N3 runs coded 'correct'
    """
    weights = {"correct": 1.0, "partial": 0.5, "confused": 0.0, "unknown": 0.0}

    sar_runs = [r for r in runs if r.prompt_category in ("N1", "N4")]
    n3_runs  = [r for r in runs if r.prompt_category == "N3"]

    sar_score = (
        sum(weights.get(r.network_resolution.value, 0.0) for r in sar_runs) / len(sar_runs)
        if sar_runs else 0.0
    )
    coherence = (
        sum(1 for r in n3_runs if r.network_resolution == NetworkResolution.correct) / len(n3_runs)
        if n3_runs else 0.0
    )
    return sar_score, coherence


# ─────────────────────────────────────────────────────────────────────────────
# Orphan list identification
# ─────────────────────────────────────────────────────────────────────────────

def _identify_orphans(
    entities: list[NetworkEntity],
    linkage_data: dict,
) -> list[str]:
    """
    Return entity IDs that are Orphan List candidates:
    entity_resolution <85 OR linkage_integrity <70 (from their Tier 2 run).
    """
    con = get_connection()
    orphan_ids: list[str] = []
    for e in entities:
        if e.inclusion_tier == OwnershipTier.affiliated or not e.linked_run_id:
            continue
        row = con.execute(
            "SELECT entity_resolution_pct, linkage_integrity_pct "
            "FROM ranked_providers WHERE run_id = ? AND rank = 1",
            [e.linked_run_id],
        ).fetchone()
        if not row:
            orphan_ids.append(e.id)
            continue
        er_pct, li_pct = row
        is_orphan = (
            (er_pct is not None and er_pct < 85) or
            (li_pct is not None and li_pct < 70) or
            (er_pct is None and li_pct is None)
        )
        if is_orphan:
            orphan_ids.append(e.id)
    con.close()
    return orphan_ids


# ─────────────────────────────────────────────────────────────────────────────
# Report narrative generation
# ─────────────────────────────────────────────────────────────────────────────

def _generate_composite_narrative(
    registry: NetworkRegistry,
    result: CompositeResult,
    on_event: Optional[Callable] = None,
) -> str:
    """Generate the Merged-Entity Delta narrative and Leakage findings summary."""
    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    client = _get_client()
    orphan_names = [
        e.name for e in registry.entities if e.id in result.orphan_entity_ids
    ]

    prompt = (
        f"You are writing the System Composite AI Visibility report for {registry.system_name} "
        f"in {registry.market_cbsa or 'the local market'}.\n\n"
        f"KEY METRICS:\n"
        f"- Hospital Score (Tier 1): {result.hospital_score:.0f}\n"
        f"- Network Score (Tier 2 avg): {result.network_score:.0f}\n"
        f"- System Attribution Rate (SAR): {result.sar:.0%}\n"
        f"- Footprint Class: {result.footprint_class.value} (W_h={result.w_h:.0%}, W_n={result.w_n:.0%})\n"
        f"- Continuum Coherence: {result.continuum_coherence:.0%} (bonus: +{result.continuum_bonus:.0f})\n"
        f"- System Composite Score: {result.composite_score:.0f} ({result.composite_grade})\n"
        f"- Merged-Entity Delta: {result.merged_entity_delta:+.0f} "
        f"({'network lifts the system' if result.merged_entity_delta > 0 else 'network is a drag'})\n"
        f"- Leakage Index: {result.leakage_index:.0%}\n"
        f"- Orphan List: {', '.join(orphan_names) if orphan_names else 'none'}\n"
        f"- Ceiling applied: {'Yes — ' + result.score_ceiling_reason if result.score_ceiling_applied else 'No'}\n\n"
        f"NETWORK ({len(registry.entities)} entities):\n"
        + "\n".join(
            f"  - {e.name} ({e.entity_type}, {e.inclusion_tier.value}, weight={e.inclusion_weight:.2f})"
            for e in registry.entities
        ) + "\n\n"
        f"Write a 3–4 paragraph System Composite narrative covering:\n"
        f"1. What the delta means and which direction value flows\n"
        f"2. SAR interpretation and what it means for brand credit vs. referral capture\n"
        f"3. Leakage findings and the top 1–2 Orphan List priorities\n"
        f"4. The most important composite-level remediation recommendation\n\n"
        f"Be specific and analytical. This is an executive-facing strategy document."
    )

    emit({"type": "text", "text": "Generating composite narrative…"})
    narrative_parts = []
    with client.messages.stream(
        model=_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            narrative_parts.append(text)
            emit({"type": "token", "token": text})

    return "".join(narrative_parts)


# ─────────────────────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────────────────────

def _save_battery_runs(runs: list[NetworkBatteryRun]) -> None:
    if not runs:
        return
    con = get_connection()
    for r in runs:
        con.execute(
            """INSERT OR IGNORE INTO network_battery_runs
               (id, registry_id, composite_run_id, prompt_category, prompt_number,
                prompt_text, assistant, retrieval_mode, response_text,
                network_resolution, run_date, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                r.id, r.registry_id, r.composite_run_id, r.prompt_category,
                r.prompt_number, r.prompt_text, r.assistant, r.retrieval_mode,
                r.response_text, r.network_resolution.value, r.run_date,
                datetime.utcnow().isoformat(),
            ],
        )
    con.close()


def _save_composite_result(result: CompositeResult) -> None:
    con = get_connection()
    con.execute(
        """INSERT OR REPLACE INTO composite_results
           (id, registry_id, anchor_run_id, hospital_score, network_score,
            attributed_network_score, sar, footprint_class, w_h, w_n,
            continuum_coherence, continuum_bonus, composite_score, composite_grade,
            merged_entity_delta, network_capture_rate, leakage_index,
            score_ceiling_applied, score_ceiling_reason, small_network_refused,
            proxy_weighted, modifier_ledger, per_assistant_sar, orphan_entity_ids,
            rubric_version_hospital, rubric_version_practice, rubric_version_composite,
            oldest_input_date, composite_expires_at, composite_mode, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            result.id, result.registry_id, result.anchor_run_id,
            result.hospital_score, result.network_score, result.attributed_network_score,
            result.sar, result.footprint_class.value, result.w_h, result.w_n,
            result.continuum_coherence, result.continuum_bonus,
            result.composite_score, result.composite_grade, result.merged_entity_delta,
            result.network_capture_rate, result.leakage_index,
            result.score_ceiling_applied, result.score_ceiling_reason,
            result.small_network_refused, result.proxy_weighted,
            json.dumps([m.dict() for m in result.modifier_ledger]),
            json.dumps(result.per_assistant_sar),
            json.dumps(result.orphan_entity_ids),
            result.rubric_version_hospital, result.rubric_version_practice,
            result.rubric_version_composite, result.oldest_input_date,
            result.composite_expires_at, result.composite_mode,
            datetime.utcnow().isoformat(),
        ],
    )
    con.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def analyze_composite(
    registry_id: str,
    output_dir: str | Path = "reports",
    on_event: Optional[Callable] = None,
    brand: str = "original",
) -> CompositeResult:
    """
    Assemble a System Composite from a confirmed NetworkRegistry.

    Reads the registry from DB, fetches Tier 1/2 scores, runs the network
    battery, applies the composite formula with all ceilings, generates the
    narrative and PDF, and returns a CompositeResult.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(e: dict) -> None:
        if on_event:
            on_event(e)

    init_db()
    emit({"type": "phase", "name": "starting", "text": "Loading network registry"})

    registry = load_registry(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    scoreable = [e for e in registry.entities if e.inclusion_tier != OwnershipTier.affiliated]
    practice_entities = [e for e in scoreable if e.entity_type == "practice"]

    # ── Staleness check ───────────────────────────────────────────────────────
    any_stale, cross_flag = _check_staleness(registry.entities)
    if any_stale:
        emit({"type": "text", "text": "⚠ Some entity runs are >90 days old — composite validity may be reduced."})

    # ── Hospital score ────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "generating", "text": "Fetching tier scores"})
    hospital_score = _fetch_hospital_score(registry.anchor_run_id)
    if hospital_score is None:
        raise ValueError("Anchor hospital run not found or has no AI Visibility Score")

    # ── Practice scores ───────────────────────────────────────────────────────
    practice_scores_map = _fetch_practice_scores(practice_entities)
    proxy_weighted = any(e.encounter_volume_share is None for e in scoreable)

    # ── Small-network check ───────────────────────────────────────────────────
    total_providers = sum(
        int(e.fte_count or 1) for e in practice_entities
    )
    if check_small_network(len(practice_entities), total_providers):
        emit({"type": "text", "text": "Small network — composite score will not be issued. Producing narrative only."})
        result = CompositeResult(
            id=str(uuid.uuid4()),
            registry_id=registry_id,
            anchor_run_id=registry.anchor_run_id,
            system_name=registry.system_name,
            hospital_score=hospital_score,
            network_score=0.0,
            attributed_network_score=0.0,
            sar=0.0,
            footprint_class=FootprintClass.facility_centric,
            w_h=1.0,
            w_n=0.0,
            continuum_coherence=0.0,
            continuum_bonus=0.0,
            composite_score=0.0,
            composite_grade="",
            merged_entity_delta=0.0,
            leakage_index=0.0,
            small_network_refused=True,
            proxy_weighted=proxy_weighted,
            cross_tier_flag=cross_flag,
            composite_mode="hospitals_and_practices",
            entities=registry.entities,
            generated_at=datetime.utcnow().isoformat(),
        )
        result.report_narrative = _generate_composite_narrative(registry, result, emit)
        _save_composite_result(result)
        return result

    # ── Network score ─────────────────────────────────────────────────────────
    p_scores = []
    p_weights = []
    for e in practice_entities:
        sc = practice_scores_map.get(e.id)
        if sc is not None:
            p_scores.append(sc)
            p_weights.append(e.inclusion_weight)

    ns = network_score(p_scores, p_weights) if p_scores else 0.0

    # ── Network battery ───────────────────────────────────────────────────────
    emit({"type": "phase", "name": "structured", "text": "Running network battery"})
    battery_runs = run_network_battery(registry, on_event)
    _save_battery_runs(battery_runs)

    battery_sar_component, continuum_coherence = _battery_resolution_score(battery_runs)

    # ── SAR computation ───────────────────────────────────────────────────────
    linkage_data, weighted_capture_rate = _fetch_linkage_data(
        practice_entities, COMPOSITE_CONFIG["integration_grace_months"]
    )
    sar = compute_sar(practice_entities, linkage_data, battery_sar_component)

    # Per-assistant SAR (Claude only for now)
    per_assistant_sar = {"claude": round(sar, 3)}

    # ── Orphan list ───────────────────────────────────────────────────────────
    orphan_ids = _identify_orphans(practice_entities, linkage_data)
    orphan_vol_share = compute_orphan_volume_share(practice_entities, set(orphan_ids))

    # ── Network encounter share (for footprint class) ─────────────────────────
    # Use client-supplied encounter_volume_share sums if available
    total_enc = sum(
        e.encounter_volume_share or 0.0
        for e in scoreable
        if e.encounter_volume_share is not None
    )
    # If no volume data, estimate from weights: practice weight share of total
    if total_enc == 0:
        practice_weight = sum(e.inclusion_weight for e in practice_entities)
        network_share   = practice_weight  # weights already sum to 1 across all scoreable
    else:
        practice_enc = sum(
            e.encounter_volume_share or 0.0
            for e in practice_entities
            if e.encounter_volume_share is not None
        )
        system_total = practice_enc + sum(
            e.encounter_volume_share or 0.0
            for e in scoreable if e.entity_type == "hospital"
            and e.encounter_volume_share is not None
        )
        network_share = practice_enc / system_total if system_total > 0 else 0.25

    # ── Leakage index ─────────────────────────────────────────────────────────
    leakage = compute_leakage_index(sar, weighted_capture_rate)

    # ── Composite formula ─────────────────────────────────────────────────────
    formula_result = compute_composite(
        hospital_score=hospital_score,
        ns=ns,
        sar=sar,
        coherence=continuum_coherence,
        modifiers_total=0.0,         # no system-level adverse modifiers from base batteries
        orphan_volume_share=orphan_vol_share,
        network_share=network_share,
    )

    # ── Expiration date ───────────────────────────────────────────────────────
    staleness = COMPOSITE_CONFIG["staleness_days"]
    composite_expires = (date.today() + timedelta(days=staleness)).isoformat()

    result = CompositeResult(
        id=str(uuid.uuid4()),
        registry_id=registry_id,
        anchor_run_id=registry.anchor_run_id,
        system_name=registry.system_name,
        hospital_score=hospital_score,
        network_score=formula_result["network_score"],
        attributed_network_score=formula_result["attributed_network_score"],
        sar=sar,
        footprint_class=FootprintClass(formula_result["footprint_class"]),
        w_h=formula_result["w_h"],
        w_n=formula_result["w_n"],
        continuum_coherence=continuum_coherence,
        continuum_bonus=formula_result["continuum_bonus"],
        composite_score=formula_result["composite_score"],
        composite_grade=formula_result["composite_grade"],
        merged_entity_delta=formula_result["merged_entity_delta"],
        leakage_index=leakage,
        score_ceiling_applied=formula_result["score_ceiling_applied"],
        score_ceiling_reason=formula_result.get("score_ceiling_reason"),
        small_network_refused=False,
        proxy_weighted=proxy_weighted,
        cross_tier_flag=cross_flag,
        per_assistant_sar=per_assistant_sar,
        orphan_entity_ids=orphan_ids,
        composite_expires_at=composite_expires,
        composite_mode="hospitals_and_practices",
        network_battery_runs=battery_runs,
        entities=registry.entities,
        generated_at=datetime.utcnow().isoformat(),
    )

    # ── Narrative ─────────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "narrative", "text": "Generating composite narrative"})
    result.report_narrative = _generate_composite_narrative(registry, result, emit)

    # ── PDF ───────────────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "pdf", "text": "Rendering composite PDF"})
    from .pdf import render_composite_pdf
    ts_str = datetime.utcnow().strftime("%y%m%d-%H%M")
    slug   = registry.system_name[:40].replace(" ", "-")
    pdf_path = output_dir / f"{slug}_SystemComposite_{ts_str}.pdf"
    render_composite_pdf(result, registry, pdf_path, brand=brand)
    result.pdf_path = str(pdf_path)

    # ── Persist ───────────────────────────────────────────────────────────────
    _save_composite_result(result)
    emit({"type": "phase", "name": "done_item", "text": "Composite complete"})
    return result
