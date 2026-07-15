"""Pulse Briefing — deterministic extraction rubric and selection engine.

Reads ONLY from the run record (AnalysisResult + RankedProvider).
No LLM calls. No network fetches.
Two calls to extract() with the same result must produce byte-identical BriefingResult.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AnalysisResult, RankedProvider

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "briefing_config.json")
_config_cache: Optional[dict] = None


def _cfg() -> dict:
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            _config_cache = json.load(fh)
    return _config_cache


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BriefingFinding:
    candidate_id: str
    finding_type: str
    candidate_type: str   # "strength" | "gap"
    source_ref: str
    what: str
    why: str
    say: str


@dataclass
class BriefingDemoPrompt:
    prompt_id: str
    prompt_text: str
    dominant_outcome: str
    dominant_pct: float
    outcome_description: str


@dataclass
class BriefingResult:
    entity_name: str
    variant: str            # "sales" | "cs"
    report_date: str
    run_id: str
    score: int
    grade: str
    band_descriptor: str
    hook: str
    findings: list          # list[BriefingFinding], exactly 3
    demo: object            # BriefingDemoPrompt | None
    objections: list        # list[dict], exactly 2 (or fewer if library sparse)
    ask: str
    prior_score: Optional[int]
    prior_date: Optional[str]
    strong_posture: bool = False   # True when all findings are strengths (degradation path)


@dataclass
class _Candidate:
    id: str
    tier_key: str
    candidate_type: str   # "strength" | "gap"
    materiality: int
    demonstrability: int
    emotional_salience: int
    actionability: int
    verification_strength: int
    source_ref: str
    finding_type: str
    context: dict = field(default_factory=dict)
    score_sales: float = 0.0
    score_cs: float = 0.0


# ── Validation ────────────────────────────────────────────────────────────────

class BriefingValidationError(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"Missing required briefing inputs: {missing}")


def validate_briefing_inputs(result: "AnalysisResult") -> list[str]:
    """Return list of missing required field names. Empty list = all present."""
    missing: list[str] = []
    p = result.rankings[0] if result.rankings else None

    if not result.entity_name:
        missing.append("entity_name")
    if not result.run_id:
        missing.append("run_id")
    if not result.generated_at:
        missing.append("generated_at")
    if not result.improvement_sections:
        missing.append("improvement_sections (roadmap required)")
    if p is None:
        missing.append("rankings[0] (no provider record)")
        return missing
    if p.ai_visibility_score is None:
        missing.append("rankings[0].ai_visibility_score")
    if not p.weighting_profile:
        missing.append("rankings[0].weighting_profile")
    ts = p.tier_scores
    for tk in ("clinical_outcomes_safety", "credentials_recognition",
               "patient_experience_reviews", "access_fit"):
        if getattr(ts, tk, None) is None:
            missing.append(f"rankings[0].tier_scores.{tk}")

    # Practice edition: anchor composite row required for full candidate pool
    if result.entity_type == "practice":
        if not result.practice_composite_rows:
            missing.append("practice_composite_rows (non-empty)")
        else:
            anchor = next((r for r in result.practice_composite_rows if r.get("is_anchor")), None)
            if not anchor:
                missing.append("practice_composite_rows: anchor row (is_anchor=True)")
            else:
                if anchor.get("avg_rating") is None:
                    missing.append("practice_composite_rows anchor: avg_rating")
                if anchor.get("total_reviews") is None:
                    missing.append("practice_composite_rows anchor: total_reviews")
    # Hospital edition (entity_type=None or "hospital"): composite rows optional

    return missing


# ── Helpers ───────────────────────────────────────────────────────────────────

_METRIC_TO_TIER = {
    "physician_capture_rate": "credentials_recognition",
    "linkage_integrity_pct":  "access_fit",
    "entity_resolution_pct":  "clinical_outcomes_safety",
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _fill(template: str, context: dict) -> str:
    """Format template with context values; unknown keys render as {key}."""
    return template.format_map(_SafeDict(context))


def _has_held_physician(result: "AnalysisResult") -> bool:
    """Return True if any physician in the composite is on the hold list."""
    try:
        from .holds import is_held
        for ph in (result.physician_composite_rows or []):
            entity = ph.get("practice_name", "") or (result.entity_name or "")
            if is_held(ph.get("physician_name", ""), entity=entity):
                return True
    except Exception:
        pass
    return False


def _anchor_verification(anchor_row: Optional[dict]) -> int:
    """Verification strength score (0–3) for the anchor composite row."""
    if not anchor_row:
        return 0
    if anchor_row.get("not_established"):
        return 0
    if not anchor_row.get("affiliation_verified", True):
        return 1
    return 3


# ── Dimension scorers ─────────────────────────────────────────────────────────

def _materiality(value_0_100: int, weight: float, ctype: str, cfg: dict) -> int:
    """Points at stake for a gap; points protected for a strength."""
    t = cfg["materiality_thresholds"]
    if ctype == "gap":
        pts = (100 - value_0_100) * weight
    else:
        pts = value_0_100 * weight
    if pts >= t["score_3"]:
        return 3
    if pts >= t["score_2"]:
        return 2
    if pts >= t["score_1"]:
        return 1
    return 0


def _demonstrability_tier(
    tier_key: str,
    anchor_row: Optional[dict],
    battery_results: Optional[dict],
    cfg: dict,
) -> int:
    if battery_results:
        eligible = cfg["demo"]["eligible_outcomes"]
        min_runs = cfg["demo"]["min_run_count"]
        min_pct = cfg["demo"]["min_dominant_pct"]
        for br in battery_results.values():
            if (br.run_count >= min_runs
                    and br.dominant_pct >= min_pct
                    and br.dominant_outcome in eligible):
                return 3
    if tier_key == "patient_experience_reviews" and anchor_row:
        if anchor_row.get("google_url"):
            return 2
        if anchor_row.get("avg_rating") is not None:
            return 1
    if tier_key in ("access_fit", "credentials_recognition"):
        return 1
    return 0


def _demonstrability_metric(
    metric_key: str,
    value: float,
    battery_results: Optional[dict],
    cfg: dict,
) -> int:
    if battery_results and metric_key == "entity_resolution_pct":
        eligible = cfg["demo"]["eligible_outcomes"]
        min_runs = cfg["demo"]["min_run_count"]
        min_pct = cfg["demo"]["min_dominant_pct"]
        for br in battery_results.values():
            if (br.run_count >= min_runs
                    and br.dominant_pct >= min_pct
                    and br.dominant_outcome in eligible):
                return 3
    # Concrete number → demonstrable in conversation
    return 1


def _emotional_salience_tier(
    tier_key: str,
    p: "RankedProvider",
    ctype: str,
    result: "AnalysisResult",
) -> int:
    """Entity resolution < 0.50 → competitor likely named → 3."""
    er = p.entity_resolution_pct
    if er is not None and er < 0.50 and ctype == "gap":
        return 3
    if tier_key == "access_fit" and ctype == "gap":
        li = p.linkage_integrity_pct
        if li is not None and li < 0.60:
            return 2
    if ctype == "strength":
        return 1
    return 0


def _emotional_salience_metric(metric_key: str, value: float, p: "RankedProvider") -> int:
    if metric_key == "entity_resolution_pct":
        return 3 if value < 0.50 else 1
    if metric_key == "linkage_integrity_pct":
        return 2 if value < 0.60 else 1
    if metric_key == "physician_capture_rate":
        return 2 if value < 0.45 else 1
    return 0


def _actionability(tier_key: str, improvement_sections: list, cfg: dict) -> int:
    """Map tier_key to improvement_section index via keyword match."""
    keywords = cfg["roadmap_tier_keywords"].get(tier_key, [])
    for idx, sec in enumerate(improvement_sections):
        title_lower = (sec.title or "").lower()
        if any(kw in title_lower for kw in keywords):
            if idx == 0:
                return 3
            if idx == 1:
                return 2
            return 1
    return 0 if not improvement_sections else 1


def _edition_labels_weights(result: "AnalysisResult", profile: str) -> tuple[dict, dict]:
    """Return (labels, weights) keyed by this entity's edition."""
    if result.entity_type == "practice":
        from .scoring import PRACTICE_TIER_LABELS
        from .practice_scoring import WEIGHTS as PW
        labels  = PRACTICE_TIER_LABELS.get(profile, PRACTICE_TIER_LABELS["practice_procedural"])
        weights = PW.get(profile, PW["practice_procedural"])
    else:
        from .scoring import TIER_LABELS, WEIGHTS
        labels  = TIER_LABELS.get(profile, TIER_LABELS["procedural"])
        weights = WEIGHTS.get(profile, WEIGHTS["procedural"])
    return labels, weights


def _compute_variant_scores(cand: _Candidate, cfg: dict) -> None:
    weights_sales = cfg["variant_weights"]["sales"]
    weights_cs = cfg["variant_weights"]["cs"]
    dims = {
        "materiality":          cand.materiality,
        "demonstrability":      cand.demonstrability,
        "emotional_salience":   cand.emotional_salience,
        "actionability":        cand.actionability,
        "verification_strength": cand.verification_strength,
    }
    cand.score_sales = sum(dims[d] * weights_sales[d] for d in dims)
    cand.score_cs = sum(dims[d] * weights_cs[d] for d in dims)


# ── Candidate pool ────────────────────────────────────────────────────────────

def _build_candidate_pool(
    result: "AnalysisResult",
    p: "RankedProvider",
    cfg: dict,
) -> list[_Candidate]:
    from .scoring import TIER_KEYS

    candidates: list[_Candidate] = []
    profile = p.weighting_profile or "procedural"
    labels, weights = _edition_labels_weights(result, profile)
    top_tier_key   = max(weights, key=lambda k: weights[k])
    top_tier_label = labels.get(top_tier_key, top_tier_key)
    has_held = _has_held_physician(result)
    bat = result.battery_results

    anchor_row = next(
        (r for r in (result.practice_composite_rows or []) if r.get("is_anchor")),
        None,
    )
    entity_name = result.entity_name or ""
    city = result.location.split(",")[0].strip() if result.location else ""
    specialty = result.specialty or "this specialty"

    # ── 1. Tier candidates ───────────────────────────────────────────────────
    for tk in TIER_KEYS:
        ts_val = getattr(p.tier_scores, tk, None)
        if ts_val is None:
            continue
        w = weights.get(tk, 0.0)
        label = labels.get(tk, tk)
        strength_thr = cfg.get("tier_thresholds", {}).get("strength", 75)
        ctype = "strength" if ts_val >= strength_thr else "gap"

        # Verification strength
        vstren = _anchor_verification(anchor_row) if tk == "patient_experience_reviews" else 3
        if vstren == 0:
            continue  # excluded (no anchor data for this tier)

        mat = _materiality(ts_val, w, ctype, cfg)
        dem = _demonstrability_tier(tk, anchor_row, bat, cfg)
        sal = _emotional_salience_tier(tk, p, ctype, result)
        act = _actionability(tk, result.improvement_sections, cfg)

        ctx: dict = {
            "entity_name":          entity_name,
            "tier_label":           label,
            "tier_score":           ts_val,
            "top_tier_label":       top_tier_label,
            "is_top_tier":          (tk == top_tier_key),
            "anchor_rating":        f"{anchor_row['avg_rating']:.1f}" if anchor_row and anchor_row.get("avg_rating") is not None else "—",
            "anchor_review_count":  anchor_row.get("total_reviews", "—") if anchor_row else "—",
            "n_platforms":          anchor_row.get("platforms_found", "—") if anchor_row else "—",
            "city":                 city,
            "specialty":            specialty,
        }
        cand = _Candidate(
            id=f"tier:{tk}",
            tier_key=tk,
            candidate_type=ctype,
            materiality=mat,
            demonstrability=dem,
            emotional_salience=sal,
            actionability=act,
            verification_strength=vstren,
            source_ref=f"Tier: {label}",
            finding_type=f"tier:{tk}:{ctype}",
            context=ctx,
        )
        _compute_variant_scores(cand, cfg)
        candidates.append(cand)

    # ── 2. Derived metric candidates ─────────────────────────────────────────
    metric_attrs = [
        ("physician_capture_rate", p.physician_capture_rate),
        ("linkage_integrity_pct",  p.linkage_integrity_pct),
        ("entity_resolution_pct",  p.entity_resolution_pct),
    ]
    for mk, mv in metric_attrs:
        if mv is None:
            continue
        thresholds = cfg["derived_metric_thresholds"][mk]
        if mv >= thresholds["strength"]:
            ctype = "strength"
        elif mv < thresholds["gap"]:
            ctype = "gap"
        else:
            continue  # neutral

        # Hold check: physician_capture_rate excluded if any physician is held
        if mk == "physician_capture_rate" and has_held:
            continue

        related_tier = _METRIC_TO_TIER[mk]
        w = weights.get(related_tier, 0.0)
        pct_str = f"{round(mv * 100)}%"
        n_phy = len(result.physician_composite_rows or [])
        n_cap = round(mv * n_phy) if n_phy > 0 else None
        n_unc = (n_phy - n_cap) if n_cap is not None else None

        mat = _materiality(int(mv * 100), w, ctype, cfg)
        dem = _demonstrability_metric(mk, mv, bat, cfg)
        sal = _emotional_salience_metric(mk, mv, p)
        act = _actionability(related_tier, result.improvement_sections, cfg)

        ctx = {
            "entity_name":           entity_name,
            f"{mk}_pct":             pct_str,
            "n_physicians":          n_phy,
            "n_captured":            n_cap,
            "n_uncaptured":          n_unc,
            "city":                  city,
            "specialty":             specialty,
        }
        cand = _Candidate(
            id=f"metric:{mk}",
            tier_key=related_tier,
            candidate_type=ctype,
            materiality=mat,
            demonstrability=dem,
            emotional_salience=sal,
            actionability=act,
            verification_strength=3,
            source_ref=f"Metric: {mk.replace('_', ' ').title()}",
            finding_type=f"metric:{mk}:{ctype}",
            context=ctx,
        )
        _compute_variant_scores(cand, cfg)
        candidates.append(cand)

    # ── 3. Score ceiling modifier ─────────────────────────────────────────────
    if p.score_ceiling_applied and p.score_ceiling_reason:
        ceiling_value = 74  # the standard cap value
        reason_plain = p.score_ceiling_reason  # already human-readable from scorer
        ctx = {
            "entity_name":              entity_name,
            "ceiling_value":            ceiling_value,
            "score_ceiling_reason":     p.score_ceiling_reason,
            "score_ceiling_reason_plain": reason_plain,
        }
        cand = _Candidate(
            id="modifier:score_ceiling",
            tier_key="clinical_outcomes_safety",
            candidate_type="gap",
            materiality=3,
            demonstrability=0,
            emotional_salience=2,
            actionability=3,
            verification_strength=3,
            source_ref="Modifier: Score Ceiling",
            finding_type="modifier:score_ceiling",
            context=ctx,
        )
        _compute_variant_scores(cand, cfg)
        candidates.append(cand)

    # ── 4. Anchor composite reputation ────────────────────────────────────────
    if (anchor_row
            and anchor_row.get("avg_rating") is not None
            and not anchor_row.get("not_established")):
        rating = anchor_row["avg_rating"]
        rev_count = anchor_row.get("total_reviews") or 0
        n_plat = anchor_row.get("platforms_found") or 0
        if rating >= 4.3 and rev_count >= 200:
            ctype = "strength"
        elif rating < 3.5 or rev_count < 50:
            ctype = "gap"
        else:
            ctype = None  # neutral — skip

        if ctype is not None:
            vstren = _anchor_verification(anchor_row)
            if vstren > 0:
                mat = 3 if rev_count >= 500 else (2 if rev_count >= 100 else 1)
                dem = 2 if anchor_row.get("google_url") else 1
                sal = 2 if ctype == "strength" else 1
                act = _actionability("patient_experience_reviews", result.improvement_sections, cfg)
                ctx = {
                    "entity_name":        entity_name,
                    "anchor_rating":      f"{rating:.1f}",
                    "anchor_review_count": rev_count,
                    "n_platforms":        n_plat,
                    "google_url":         anchor_row.get("google_url"),
                }
                cand = _Candidate(
                    id="composite:anchor_reputation",
                    tier_key="patient_experience_reviews",  # competes with tier slot
                    candidate_type=ctype,
                    materiality=mat,
                    demonstrability=dem,
                    emotional_salience=sal,
                    actionability=act,
                    verification_strength=vstren,
                    source_ref="Composite: Reputation",
                    finding_type=f"composite:anchor_reputation:{ctype}",
                    context=ctx,
                )
                _compute_variant_scores(cand, cfg)
                candidates.append(cand)

    return candidates


# ── Selection ──────────────────────────────────────────────────────────────────

def _select_findings(
    candidates: list[_Candidate], variant: str, cfg: dict
) -> tuple[list[_Candidate], bool]:
    """Select exactly 3 findings: 1-2 strengths, 1-2 gaps (composition constraint).

    Returns (selected, strong_posture).
    strong_posture=True when fewer gaps than required are available (degradation path).
    """
    score_attr = "score_sales" if variant == "sales" else "score_cs"
    comp = cfg.get("composition", {}).get(variant, {"required_strengths": 1, "required_gaps": 2})
    n_s_req = comp["required_strengths"]
    n_g_req = comp["required_gaps"]

    def _sort(pool: list) -> list:
        return sorted(pool, key=lambda c: (-getattr(c, score_attr), c.id))

    def _pick(pool: list, n: int, used: set) -> list[_Candidate]:
        result: list[_Candidate] = []
        for c in _sort(pool):
            if len(result) >= n:
                break
            if c.tier_key not in used:
                result.append(c)
                used.add(c.tier_key)
        return result

    strength_pool = [c for c in candidates if c.candidate_type == "strength"]
    gap_pool      = [c for c in candidates if c.candidate_type == "gap"]

    used_tiers: set[str] = set()
    selected_g = _pick(gap_pool, n_g_req, used_tiers)
    selected_s = _pick(strength_pool, n_s_req, used_tiers)
    selected   = selected_g + selected_s

    # Fill any remaining slot (up to 3) from whichever pool has candidates left
    if len(selected) < 3:
        remaining = [c for c in candidates if c not in selected]
        selected += _pick(remaining, 3 - len(selected), used_tiers)

    # Degradation: if no strength candidates exist at all, re-classify the
    # highest-scoring gap as a relative strength so the hook contrast still works
    if selected and not any(c.candidate_type in ("strength", "relative_strength") for c in selected):
        import copy
        best = _sort(selected)[0]
        promoted = copy.copy(best)
        promoted.candidate_type = "relative_strength"
        promoted.finding_type   = promoted.finding_type.replace(":gap", ":relative_strength")
        selected[selected.index(best)] = promoted

    actual_gaps = sum(1 for c in selected if c.candidate_type == "gap")
    strong_posture = actual_gaps < n_g_req

    if len(selected) < 3:
        raise BriefingValidationError(["insufficient_distinct_findings"])

    return selected, strong_posture


# ── Auto-selected elements ─────────────────────────────────────────────────────

def _build_hook(
    findings: list[_Candidate],
    result: "AnalysisResult",
    p: "RankedProvider",
    cfg: dict,
    strong_posture: bool = False,
) -> str:
    from .scoring import grade_from_score
    score = p.ai_visibility_score or 0
    grade, _ = grade_from_score(score)
    band = cfg["band_descriptors"].get(grade, "moderate AI visibility")
    entity_name = result.entity_name or ""
    has_strength = any(f.candidate_type in ("strength", "relative_strength") for f in findings)
    has_gap = any(f.candidate_type == "gap" for f in findings)

    def _label(c: _Candidate) -> str:
        return c.source_ref.split(": ", 1)[-1]

    if has_strength and has_gap:
        s = next(f for f in findings if f.candidate_type in ("strength", "relative_strength"))
        g = next(f for f in findings if f.candidate_type == "gap")
        tpl = cfg["hook_templates"]["contrast"]
        return _fill(tpl, {
            "entity_name": entity_name,
            "strength_label": _label(s),
            "gap_label": _label(g),
            "score": score,
            "grade": grade,
            "band_descriptor": band,
        })
    elif not has_strength:
        top_gap = next(f for f in findings if f.candidate_type == "gap")
        tpl = cfg["hook_templates"]["problem_lead"]
        return _fill(tpl, {
            "entity_name": entity_name,
            "score": score,
            "grade": grade,
            "band_descriptor": band,
            "top_gap_label": _label(top_gap),
        })
    else:
        tpl = cfg["hook_templates"]["strength_lead"]
        hook = _fill(tpl, {
            "entity_name": entity_name,
            "score": score,
            "grade": grade,
            "band_descriptor": band,
        })
        if strong_posture:
            note = cfg.get("composition", {}).get("strong_posture_note", "")
            if note:
                hook = hook + " " + note
        return hook


def _select_demo(result: "AnalysisResult", cfg: dict) -> Optional[BriefingDemoPrompt]:
    bat = result.battery_results
    if not bat:
        return None
    dcfg = cfg["demo"]
    eligible = []
    for pid, br in bat.items():
        if (br.run_count >= dcfg["min_run_count"]
                and br.dominant_pct >= dcfg["min_dominant_pct"]
                and br.dominant_outcome in dcfg["eligible_outcomes"]):
            eligible.append((pid, br))
    if not eligible:
        return None
    # Pick highest dominant_pct; tie-break by prompt_id
    eligible.sort(key=lambda x: (-x[1].dominant_pct, x[0]))
    pid, br = eligible[0]
    outcome_tpl = cfg["demo_outcome_descriptions"].get(
        br.dominant_outcome, f"{br.dominant_outcome} ({round(br.dominant_pct * 100)}% of runs)"
    )
    description = _fill(outcome_tpl, {"entity_name": result.entity_name or ""})
    return BriefingDemoPrompt(
        prompt_id=pid,
        prompt_text=br.prompt_text,
        dominant_outcome=br.dominant_outcome,
        dominant_pct=br.dominant_pct,
        outcome_description=description,
    )


def _select_objections(
    findings: list[_Candidate],
    variant: str,
    cfg: dict,
    context: dict,
) -> list[dict]:
    finding_types = {f.finding_type for f in findings}
    scored: list[tuple] = []
    for obj in cfg["objections"]:
        if variant not in obj.get("variants", []):
            continue
        triggers = set(obj.get("triggers", []))
        overlap = len(triggers & finding_types)
        if overlap > 0:
            scored.append((overlap, obj["id"], obj))
    scored.sort(key=lambda x: (-x[0], x[1]))
    result_objs = []
    for _, _, obj in scored:
        if len(result_objs) >= 2:
            break
        filled_rebuttal = _fill(obj["rebuttal"], context)
        result_objs.append({
            "id":        obj["id"],
            "objection": obj["objection"],
            "rebuttal":  filled_rebuttal,
        })
    return result_objs


def _get_prior(result: "AnalysisResult") -> dict:
    """Return {score, generated_at} for the most recent prior run, or empty dict."""
    try:
        from .db import get_connection
        con = get_connection()
        cur_row = con.execute(
            "SELECT generated_at FROM analysis_runs WHERE run_id = ?",
            [result.run_id],
        ).fetchone()
        if not cur_row:
            con.close()
            return {}
        cur_date = cur_row[0]
        prior_row = con.execute(
            """SELECT result_json, generated_at FROM analysis_runs
               WHERE entity_name = ? AND location = ? AND generated_at < ?
               ORDER BY generated_at DESC LIMIT 1""",
            [result.entity_name, result.location, cur_date],
        ).fetchone()
        con.close()
        if not prior_row:
            return {}
        rj = json.loads(prior_row[0])
        rankings = rj.get("rankings") or []
        prior_score = rankings[0].get("ai_visibility_score") if rankings else None
        return {"score": prior_score, "generated_at": prior_row[1]}
    except Exception:
        return {}


def _build_ask(
    findings: list[_Candidate],
    result: "AnalysisResult",
    p: "RankedProvider",
    variant: str,
    cfg: dict,
) -> tuple[str, Optional[int], Optional[str]]:
    """Return (ask_text, prior_score_or_None, prior_date_or_None)."""
    sections = result.improvement_sections or []
    section_title = sections[0].title if sections else "Phase 1"
    roadmap_item_1 = sections[0].items[0] if sections and sections[0].items else "optimize AI visibility signals"
    roadmap_item_2 = sections[1].items[0] if len(sections) > 1 and sections[1].items else None
    ri2_clause = f" and {roadmap_item_2}" if roadmap_item_2 else ""
    top_gap = next((f for f in findings if f.candidate_type == "gap"), findings[0])
    top_gap_label = top_gap.source_ref.split(": ", 1)[-1]

    ctx = {
        "entity_name":          result.entity_name or "",
        "roadmap_section_title": section_title,
        "roadmap_item_1":       roadmap_item_1,
        "roadmap_item_2":       roadmap_item_2 or "",
        "roadmap_item_2_clause": ri2_clause,
        "top_gap_label":        top_gap_label,
    }

    if variant == "sales":
        return _fill(cfg["ask_templates"]["sales"], ctx), None, None

    prior = _get_prior(result)
    prior_score = prior.get("score")
    prior_date  = prior.get("generated_at")
    if prior_score is not None:
        ctx.update({
            "prior_date":  prior_date or "",
            "prior_score": prior_score,
            "score":       p.ai_visibility_score or 0,
            "delta":       abs((p.ai_visibility_score or 0) - prior_score),
        })
        ask = _fill(cfg["ask_templates"]["cs_with_prior"], ctx)
    else:
        ask = _fill(cfg["ask_templates"]["cs_no_prior"], ctx)
    return ask, prior_score, prior_date


def _render_finding(cand: _Candidate, cfg: dict) -> BriefingFinding:
    tpl = cfg["finding_templates"].get(cand.finding_type)
    if tpl is None and cand.candidate_type == "relative_strength":
        tpl = cfg["finding_templates"].get("tier:_relative_strength")
    if tpl:
        what = _fill(tpl["what"], cand.context)
        why  = _fill(tpl["why"],  cand.context)
        say  = _fill(tpl["say"],  cand.context)
    else:
        # Fallback for any finding type without a template
        what = f"{cand.context.get('entity_name', '')} — {cand.source_ref} ({cand.candidate_type})."
        why  = "This signal affects the AI Visibility Score."
        say  = "Our analysis identifies this as an area to address."
    return BriefingFinding(
        candidate_id=cand.id,
        finding_type=cand.finding_type,
        candidate_type=cand.candidate_type,
        source_ref=cand.source_ref,
        what=what,
        why=why,
        say=say,
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def extract(result: "AnalysisResult", variant: str) -> BriefingResult:
    """Extract a BriefingResult from the run record.

    Two calls with the same result and variant must produce byte-identical output.
    Raises BriefingValidationError if required inputs are missing.
    """
    if variant not in ("sales", "cs"):
        raise ValueError(f"variant must be 'sales' or 'cs', got {variant!r}")

    # Edition assertion: rubric must match entity_type from registry
    entity_type = result.entity_type  # "practice" | "hospital" | None (= hospital)
    edition = "practice" if entity_type == "practice" else "hospital"

    missing = validate_briefing_inputs(result)
    if missing:
        raise BriefingValidationError(missing)

    p = result.rankings[0]
    cfg = _cfg()

    from .scoring import grade_from_score
    score = p.ai_visibility_score or 0
    grade, _ = grade_from_score(score)
    band = cfg["band_descriptors"].get(grade, "moderate AI visibility")

    candidates = _build_candidate_pool(result, p, cfg)
    findings_raw, strong_posture = _select_findings(candidates, variant, cfg)

    # Build shared context for objections
    anchor_row = next(
        (r for r in (result.practice_composite_rows or []) if r.get("is_anchor")),
        None,
    )
    gap_finding = next(
        (f for f in findings_raw if f.candidate_type == "gap"),
        findings_raw[0],
    )
    obj_ctx = {
        "entity_name":              result.entity_name or "",
        "gap_label":                gap_finding.source_ref.split(": ", 1)[-1],
        "ceiling_value":            74,
        "score_ceiling_reason_plain": (p.score_ceiling_reason or ""),
    }

    findings_rendered = [_render_finding(f, cfg) for f in findings_raw]
    hook = _build_hook(findings_raw, result, p, cfg, strong_posture)
    demo = _select_demo(result, cfg)
    objections = _select_objections(findings_raw, variant, cfg, obj_ctx)
    ask, prior_score, prior_date = _build_ask(findings_raw, result, p, variant, cfg)

    return BriefingResult(
        entity_name=result.entity_name or "",
        variant=variant,
        report_date=result.generated_at.isoformat() if result.generated_at else "",
        run_id=result.run_id,
        score=score,
        grade=grade,
        band_descriptor=band,
        hook=hook,
        findings=findings_rendered,
        demo=demo,
        objections=objections,
        ask=ask,
        prior_score=prior_score,
        prior_date=prior_date,
        strong_posture=strong_posture,
    )
