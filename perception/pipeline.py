"""Unified individual-report pipeline (heart-surgery item 6, increment B).

One ordered set of SHARED phases — setup, cache, evidence, prompt, narrative,
structured extraction, build + score, assemble, markdown, sources, truncation
gate, plain-language condense, PDF, save, briefing, done — composed with a small
TYPE ADAPTER (hospital / practice / community_health) that supplies only what
genuinely differs: which evidence is gathered, which prompt and extraction tool
are used, how the rankings are built and scored, which extra result fields,
filename stem, PDF renderer and DB extras apply.

Every adapter method calls the existing type-specific helpers in analyzer.py,
practice_analyzer.py and fqhc_analyzer.py — nothing is re-implemented here, so
the legacy entry points (analyze_location / analyze_practice / analyze_fqhc)
and this pipeline run the same code for every scoring-relevant step. The legacy
functions remain importable and selectable (server: PULSE_PIPELINE=legacy).

Reconciliations versus the legacy functions (deliberate):
  * cache: the same-day lock and the 30-day cache both key on aggregate for every
    type (the hospital and FQHC paths ignored it — a hospital aggregate and a
    single run shared one slot).
  * hospital runs persist entity_type='hospital' on analysis_runs (the cache no
    longer depends on NULL meaning "hospital").
  * the truncation gate runs AFTER the FQHC MQCR battery (legacy ran it before,
    so a battery-restored score still lost its PDF).
  * one briefing implementation (BriefingValidationError, briefing_ready /
    briefing_skipped events), gated on `briefing_variant and not skip_pdf`,
    for all three types.
  * completion is signalled with phase:done_item for all three types (FQHC
    emitted a bare {"type": "done"} which the SSE consumer treats as stream end).
  * PDF render errors propagate to the job (FQHC swallowed them).
  * a hospital run whose extraction omits weighting_profile falls back to
    scoring.classify_profile (legacy referenced an undefined `evidence`).
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from . import scoring
from .config import settings
from .data import places
from .db import get_connection, init_db
from .models import AnalysisResult
from .strings import FULL_DISCLAIMER as _FULL_DISCLAIMER

from . import analyzer as _hosp
from . import practice_analyzer as _prac
from . import fqhc_analyzer as _fqhc
from .analyzer import (
    _MODEL,
    _clean,
    _gather_individual_evidence,
    _get_client,
    _save_to_db,
    _stream_narrative,
    _warn_snake_case,
    last_web_search_used,
    pop_last_sources,
)

# entity_type argument → analysis_runs.entity_type key used for cache lookups
_TYPE_KEYS = {
    "hospital": "hospital",
    "practice": "practice",
    "service_line": "practice",
    "community_health": "community_health",
}


class _Ctx:
    """Mutable per-run state handed to every phase and adapter method."""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)

    def __getattr__(self, name):          # unset optional state reads as None
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def _confirm_city(read, city: str, emit) -> None:
    """Emit confirm_city when the verified Google address disagrees with the input city."""
    if read is not None and getattr(read, "formatted_address", None):
        _ratio = places.city_match_ratio(city, read.formatted_address)
        if _ratio < 0.85:
            _found_city = places._city_from_address(read.formatted_address)
            if _found_city:
                emit({"type": "confirm_city", "input_city": city,
                      "suggested_city": _found_city, "ratio": round(_ratio, 3)})


def _run_structured_extraction(client, console, tool: dict, tool_name: str, max_tokens: int,
                               prompt: str, label: str) -> dict:
    """Phase 6 — forced tool call that returns the structured record.

    Same call shape as the legacy analyzers (no temperature: newer SDKs reject it)."""
    structured_data: dict = {}
    with console.status(f"[bold dark_sea_green4]Extracting {label} structured data…[/bold dark_sea_green4]"):
        with client.messages.stream(
            model=_MODEL,
            max_tokens=max_tokens,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        console.print(f"[yellow]⚠[/yellow] {label} extraction hit token limit — partial data only")
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            structured_data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break
    return structured_data


# ═════════════════════════════════════════════════════════════════════════════
# Type adapters
# ═════════════════════════════════════════════════════════════════════════════

class _Adapter:
    type_key = ""            # analysis_runs.entity_type key for the cache
    label = ""               # phase-text / console label
    gate_noun = "entity"     # noun in the truncation-gate message

    # 3 — evidence: sets ctx.evidence_text (+ ctx.read / ctx.quality / type state)
    def evidence(self, ctx: _Ctx) -> None:
        raise NotImplementedError

    # 4 — prompt
    def prompt(self, ctx: _Ctx) -> tuple[str, str]:
        raise NotImplementedError

    # 6 — extraction spec: (tool, tool_name, max_tokens, prompt)
    def extraction(self, ctx: _Ctx) -> tuple[dict, str, int, str]:
        raise NotImplementedError

    # 7 — build + score: returns rankings; sets ctx.run_profile
    def build_and_score(self, ctx: _Ctx, structured: dict) -> list:
        raise NotImplementedError

    # 8 — assemble
    def disclaimer(self, ctx: _Ctx) -> str:
        return _FULL_DISCLAIMER

    def coverage_note(self, ctx: _Ctx) -> str:
        return f"Individual entity report: {ctx.entity_name}"

    def improvement_sections(self, ctx: _Ctx, structured: dict) -> list:
        return _hosp._improvement_sections(structured)

    def practical_advice(self, ctx: _Ctx, structured: dict) -> list:
        return []

    def extra_fields(self, ctx: _Ctx) -> dict:
        return {}

    def post_assemble(self, ctx: _Ctx, result: AnalysisResult) -> None:
        return None

    # 9 — filename stem
    def stem(self, ctx: _Ctx) -> str:
        raise NotImplementedError

    # 10 — sources
    def extra_sources(self, ctx: _Ctx, result: AnalysisResult) -> list:
        return []

    # 13 — PDF
    def render_pdf(self, ctx: _Ctx, result: AnalysisResult, pdf_path: Path) -> None:
        from .pdf import render_pdf
        render_pdf(result, pdf_path, brand=ctx.brand)

    # 14 — DB extras
    def save_extras(self, ctx: _Ctx, result: AnalysisResult) -> None:
        return None


class HospitalAdapter(_Adapter):
    type_key = "hospital"
    label = "analysis"
    gate_noun = "entity"

    def evidence(self, ctx: _Ctx) -> None:
        emit, console = ctx.emit, ctx.console
        emit({"type": "phase", "name": "evidence", "text": "Gathering verified evidence"})
        ctx.quality = None
        ctx.read = None
        with console.status("[bold dark_sea_green4]Fetching entity Google data…[/bold dark_sea_green4]"):
            try:
                ctx.evidence_text, ctx.read, ctx.quality = _gather_individual_evidence(
                    ctx.entity_name, ctx.city, ctx.state, ctx.entity_type)
                if ctx.quality:
                    _q = ctx.quality
                    emit({"type": "text", "text": f"\nVerified quality signals — CMS overall stars: {(str(_q['cms_star']) + '★') if _q.get('cms_star') else ('not rated' if _q.get('cms_facility_id') else 'no record')} · Leapfrog grade: {_q.get('leapfrog_grade') or 'not rated / not found'}\n"})
                _confirm_city(ctx.read, ctx.city, emit)
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
                ctx.evidence_text = f"=== Evidence for {ctx.entity_name}, {ctx.city}, {ctx.state} ===\nGoogle data unavailable this run."
        console.print(f"[green]✓[/green] Individual report: {ctx.entity_name}")

    def prompt(self, ctx: _Ctx) -> tuple[str, str]:
        from .prompts import build_individual_prompt
        return build_individual_prompt(
            entity_name=ctx.entity_name, city=ctx.city, state=ctx.state,
            specialty=ctx.specialty, evidence_block=ctx.evidence_text, aggregate=ctx.aggregate,
        )

    def extraction(self, ctx: _Ctx) -> tuple[dict, str, int, str]:
        return (_hosp._STRUCTURED_OUTPUT_TOOL, "submit_analysis_result", 32000,
                _hosp._hospital_extraction_prompt(True, ctx.entity_type, ctx.evidence_text, ctx.report_markdown))

    def build_and_score(self, ctx: _Ctx, structured: dict) -> list:
        emit, console = ctx.emit, ctx.console
        run_profile = structured.get("weighting_profile") or scoring.classify_profile(
            ctx.specialty, "specialty" if ctx.specialty else "hospital")
        run_profile, ctx.rubric_note = _hosp._pin_hospital_rubric(run_profile, ctx.entity_type, emit, console)
        ctx.run_profile = run_profile
        rankings = [_hosp._build_provider(r, run_profile) for r in structured.get("rankings", [])]
        _hosp._dedup_consolidated_locations(rankings)

        emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
        _hosp._apply_verified_quality(rankings, ctx.quality)          # CMS / Leapfrog override
        for prov in rankings:
            do_system = ctx.aggregate and settings.enable_system_reputation
            _hosp._ground_and_score(prov, ctx.city, ctx.state, {}, {}, do_system=do_system)
        console.print(f"[green]✓[/green] Scored {len(rankings)} providers (0 system aggregates)")

        if not ctx.practice_composite and not ctx.physician_composite and ctx.entity_type != "practice":
            _hosp._sync_entity_scores(rankings, ctx.city, ctx.state, run_profile, ctx.run_id,
                                      ctx.override_today_lock, source="deep_diagnostic")
        return rankings

    def practical_advice(self, ctx: _Ctx, structured: dict) -> list:
        return [_clean(a) for a in structured.get("practical_advice", []) if isinstance(a, str)]

    def extra_fields(self, ctx: _Ctx) -> dict:
        return dict(
            zip_code=ctx.zip_code,
            radius_miles=ctx.radius_miles,
            patient_perspective=bool(ctx.patient_perspective or ctx.teaser_report or ctx.simplified),
            simplified=bool(ctx.simplified),
            obscure_competitors=ctx.obscure_competitors if ctx.obscure_competitors is not None else True,
            target_entity=ctx.target_entity,
            entity_type=ctx.entity_type,
            rubric_note=ctx.rubric_note or "",
        )

    def post_assemble(self, ctx: _Ctx, result: AnalysisResult) -> None:
        _hosp._apply_simplified_target(
            result, simplified=ctx.simplified, obscure_competitors=ctx.obscure_competitors,
            target_entity=ctx.target_entity, service_line=ctx.service_line, parent_system=ctx.parent_system,
            city=ctx.city, state=ctx.state, specialty=ctx.specialty, brand=ctx.brand,
            force_rerun=ctx.force_rerun, override_today_lock=ctx.override_today_lock, emit=ctx.emit,
        )
        if ctx.practice_composite:
            _hosp._collect_hospital_practice_composite(
                result, ctx.entity_name, ctx.city, ctx.state, ctx.practice_roster, ctx.physician_composite,
                ctx.physician_roster, ctx.emit, ctx.force_rerun,
            )

    def stem(self, ctx: _Ctx) -> str:
        return _hosp._individual_stem(ctx.entity_name, ctx.city, ctx.state, ctx.teaser_report)

    def extra_sources(self, ctx: _Ctx, result: AnalysisResult) -> list:
        if not ctx.quality:
            return []
        result.verified_quality = ctx.quality
        try:
            from .data import quality as _quality
            return _quality.sources(ctx.quality)
        except Exception:
            return []

    def save_extras(self, ctx: _Ctx, result: AnalysisResult) -> None:
        # NEW: persist the type explicitly so the cache no longer relies on NULL == hospital.
        con = get_connection()
        try:
            con.execute("UPDATE analysis_runs SET entity_type = ? WHERE run_id = ?", ["hospital", result.run_id])
        finally:
            con.close()


class PracticeAdapter(_Adapter):
    type_key = "practice"
    label = "practice analysis"
    gate_noun = "practice"

    def evidence(self, ctx: _Ctx) -> None:
        emit, console = ctx.emit, ctx.console
        from .practice_models import PROFILE_DISPLAY, classify_practice_profile
        if not ctx.practice_profile:
            ctx.practice_profile = classify_practice_profile(ctx.specialty)
        ctx.profile_label = PROFILE_DISPLAY.get(ctx.practice_profile, "Procedural")

        emit({"type": "phase", "name": "evidence", "text": "Gathering practice evidence"})
        ctx.read = None
        with console.status("[bold dark_sea_green4]Fetching entity Google data…[/bold dark_sea_green4]"):
            try:
                ctx.evidence_text, ctx.read, _ = _gather_individual_evidence(ctx.entity_name, ctx.city, ctx.state, "practice")
                _confirm_city(ctx.read, ctx.city, emit)
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
                ctx.evidence_text = (
                    f"=== Evidence for {ctx.entity_name}, {ctx.city}, {ctx.state} ===\n"
                    "Google data unavailable this run."
                )
        console.print(f"[green]✓[/green] Practice evidence: {ctx.entity_name}")

        # Anchor's normalized street for address-based composite dedup.
        ctx.anchor_addr_norm = ""
        if ctx.read is not None and getattr(ctx.read, "formatted_address", None):
            ctx.anchor_addr_norm = _prac._normalize_street(ctx.read.formatted_address.split(",")[0])

        # Roster: confirmed_siblings passthrough / service-line discovery / sibling discovery.
        ctx.location_roster, ctx.aggregate_siblings, ctx.org_name = _prac._resolve_practice_roster(
            ctx.entity_name, ctx.city, ctx.state, aggregate=ctx.aggregate,
            confirmed_siblings=ctx.confirmed_siblings, service_line=ctx.service_line,
            parent_system=ctx.parent_system, org_name=ctx.org_name, emit=emit, force_rerun=ctx.force_rerun,
        )
        if ctx.org_name and not ctx.report_title:
            ctx.report_title = ctx.org_name

        # Anchor pinning + roster Google pre-pass.
        ctx.anchor_google, ctx.roster_rep, _suffix = _prac._roster_google_prepass(
            ctx.entity_name, ctx.city, ctx.state, anchor_listing=ctx.anchor_listing,
            aggregate_siblings=ctx.aggregate_siblings, emit=emit, console=console,
        )
        ctx.evidence_text += _suffix
        ctx.roster_fp = (_prac._roster_key(ctx.entity_name, ctx.aggregate_siblings)
                         if ctx.aggregate_siblings is not None else "")

    def prompt(self, ctx: _Ctx) -> tuple[str, str]:
        from .practice_prompts import build_practice_prompt
        return build_practice_prompt(
            entity_name=ctx.entity_name, city=ctx.city, state=ctx.state, specialty=ctx.specialty,
            evidence_block=ctx.evidence_text + (ctx.extra_evidence or ""),
            aggregate=ctx.aggregate, practice_profile=ctx.practice_profile,
            location_roster=ctx.location_roster, org_name=ctx.org_name,
        )

    def extraction(self, ctx: _Ctx) -> tuple[dict, str, int, str]:
        return (_prac._PRACTICE_TOOL, "submit_practice_result", 16000,
                _prac._practice_extraction_prompt(ctx.report_markdown, ctx.location_roster, ctx.entity_name))

    def build_and_score(self, ctx: _Ctx, structured: dict) -> list:
        emit, console = ctx.emit, ctx.console
        run_profile = structured.get("weighting_profile") or ctx.practice_profile
        ctx.run_profile = run_profile
        # Derived metrics from the tool output.
        ctx.entity_resolution_pct = structured.get("entity_resolution_pct")
        ctx.linkage_integrity_pct = structured.get("linkage_integrity_pct")
        ctx.physician_capture_rate = None   # not AI-generated; computed from battery logs when available
        ctx.board_cert_unverifiable = bool(structured.get("board_cert_unverifiable", False))
        ctx.key_person_flag = bool(structured.get("key_person_flag", False))

        rankings = [_prac._build_practice_provider(r, run_profile) for r in structured.get("rankings", [])]

        emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
        for _i, prov in enumerate(rankings):
            _prac._ground_and_score_practice(
                prov, ctx.city, ctx.state,
                entity_resolution_pct=ctx.entity_resolution_pct,
                linkage_integrity_pct=ctx.linkage_integrity_pct,
                board_cert_unverifiable=ctx.board_cert_unverifiable,
                roster_rep=ctx.roster_rep if _i == 0 else None,
                pinned_anchor=ctx.anchor_google if (_i == 0 and (ctx.anchor_google or {}).get("place_id")) else None,
            )
        console.print(f"[green]✓[/green] Scored practice ({run_profile} / {ctx.profile_label})")

        # rankings[0] sync with the roster_key gate.
        _prac._sync_practice_entity_score(rankings, ctx.city, ctx.state, ctx.run_id,
                                          ctx.override_today_lock, run_profile, ctx.roster_fp or "")
        return rankings

    def coverage_note(self, ctx: _Ctx) -> str:
        return f"Individual practice report: {ctx.entity_name}"

    def improvement_sections(self, ctx: _Ctx, structured: dict) -> list:
        return _hosp._improvement_sections(structured, drop=_prac._hospital_signal)

    def extra_fields(self, ctx: _Ctx) -> dict:
        return dict(
            patient_perspective=False,
            entity_type="practice",
            rubric_version=_prac._RUBRIC_VERSION,
            practice_profile=ctx.run_profile,
            service_line=ctx.service_line or None,
            parent_system=ctx.parent_system or None,
        )

    def post_assemble(self, ctx: _Ctx, result: AnalysisResult) -> None:
        if ctx.practice_composite:
            _prac._collect_practice_composite(
                result, result.rankings, ctx.entity_name, ctx.city, ctx.state,
                anchor_google=ctx.anchor_google or {}, aggregate_siblings=ctx.aggregate_siblings,
                practice_roster=ctx.practice_roster, anchor_addr_norm=ctx.anchor_addr_norm or "",
                physician_composite=bool(ctx.physician_composite), physician_roster=ctx.physician_roster,
                emit=ctx.emit, force_rerun=ctx.force_rerun,
            )

    def stem(self, ctx: _Ctx) -> str:
        return _prac._practice_stem(ctx.entity_name, ctx.city, ctx.state, ctx.teaser_report)

    def save_extras(self, ctx: _Ctx, result: AnalysisResult) -> None:
        _prac._save_practice_extras(
            result,
            entity_resolution_pct=ctx.entity_resolution_pct,
            linkage_integrity_pct=ctx.linkage_integrity_pct,
            physician_capture_rate=ctx.physician_capture_rate,
            key_person_flag=bool(ctx.key_person_flag),
        )


class CommunityHealthAdapter(_Adapter):
    type_key = "community_health"
    label = "Community Health analysis"
    gate_noun = "health center"

    def evidence(self, ctx: _Ctx) -> None:
        emit, console = ctx.emit, ctx.console
        emit({"type": "phase", "name": "evidence", "text": "Gathering FQHC evidence"})
        ctx.read = None
        with console.status("[bold dark_sea_green4]Fetching Google data…[/bold dark_sea_green4]"):
            try:
                ctx.evidence_text, ctx.read, _ = _gather_individual_evidence(ctx.entity_name, ctx.city, ctx.state, "community_health")
            except Exception as exc:
                console.print(f"[yellow]⚠[/yellow] Google fetch failed ({exc}); proceeding model-only.")
                ctx.evidence_text = (
                    f"=== Evidence for {ctx.entity_name}, {ctx.city}, {ctx.state} ===\n"
                    "Google data unavailable this run."
                )
        # HRSA lookup + intake synthesis.
        ctx.hrsa_data = _fqhc._lookup_hrsa(ctx.entity_name, ctx.city, ctx.state, emit, console)
        if ctx.fqhc_intake is None:
            ctx.fqhc_intake = _fqhc._default_intake(ctx.hrsa_data)
        console.print(f"[green]✓[/green] FQHC evidence: {ctx.entity_name}")

    def prompt(self, ctx: _Ctx) -> tuple[str, str]:
        from .fqhc_prompts import build_fqhc_prompt
        return build_fqhc_prompt(
            entity_name=ctx.entity_name, city=ctx.city, state=ctx.state,
            evidence_block=ctx.evidence_text, intake=ctx.fqhc_intake, hrsa_data=ctx.hrsa_data,
            aggregate=ctx.aggregate, site_roster=ctx.site_roster,
        )

    def extraction(self, ctx: _Ctx) -> tuple[dict, str, int, str]:
        return (_fqhc._FQHC_TOOL, "submit_fqhc_result", 16000,
                _fqhc._fqhc_extraction_prompt(ctx.report_markdown))

    def build_and_score(self, ctx: _Ctx, structured: dict) -> list:
        emit, console = ctx.emit, ctx.console
        ctx.run_profile = "community_health"
        ctx.pillar_scores, ctx.fact_audit_rows, ctx.missed_queries = _fqhc._parse_fqhc_structured(structured)
        rankings = [_fqhc._build_fqhc_provider(r) for r in structured.get("rankings", [])]
        _fqhc._supplement_sites(rankings, ctx.hrsa_data, ctx.site_roster)     # HRSA site supplement

        emit({"type": "phase", "name": "scoring", "text": "Verifying Google + scoring"})
        for prov in rankings:
            _fqhc._ground_and_score_fqhc(prov, ctx.city, ctx.state, ctx.pillar_scores)
        console.print(f"[green]✓[/green] Scored FQHC ({ctx.entity_name})")
        return rankings

    def disclaimer(self, ctx: _Ctx) -> str:
        return _fqhc._FQHC_DISCLAIMER

    def coverage_note(self, ctx: _Ctx) -> str:
        return f"Community Health Edition report: {ctx.entity_name}"

    def extra_fields(self, ctx: _Ctx) -> dict:
        return dict(
            entity_type="community_health",
            rubric_version=_fqhc._RUBRIC_VERSION,
            fqhc_intake=ctx.fqhc_intake,
            fqhc_pillar_scores=ctx.pillar_scores,
            fqhc_fact_audit=ctx.fact_audit_rows or [],
            fqhc_missed_queries=ctx.missed_queries or [],
            fqhc_mqcr=None,   # populated by the battery in post_assemble
        )

    def post_assemble(self, ctx: _Ctx, result: AnalysisResult) -> None:
        _fqhc._run_mqcr_battery(result, ctx.run_id, ctx.entity_name, ctx.city, ctx.state,
                                ctx.hrsa_data or {}, ctx.emit, ctx.console)

    def stem(self, ctx: _Ctx) -> str:
        return _fqhc._fqhc_stem(ctx.entity_name, ctx.teaser_report, ctx.run_id)

    def render_pdf(self, ctx: _Ctx, result: AnalysisResult, pdf_path: Path) -> None:
        from .fqhc_pdf import render_fqhc_pdf
        render_fqhc_pdf(result, str(pdf_path), brand=ctx.brand)

    def save_extras(self, ctx: _Ctx, result: AnalysisResult) -> None:
        _fqhc._save_fqhc_extras(result)


_ADAPTERS = {
    "hospital": HospitalAdapter,
    "practice": PracticeAdapter,
    "service_line": PracticeAdapter,
    "community_health": CommunityHealthAdapter,
}


# ═════════════════════════════════════════════════════════════════════════════
# The pipeline
# ═════════════════════════════════════════════════════════════════════════════

def run_individual(
    entity_type: str,
    entity_name: str,
    city: str,
    state: str,
    *,
    specialty: Optional[str] = None,
    aggregate: bool = False,
    output_dir: str | Path = "reports",
    on_event: Optional[Callable] = None,
    brand: str = "original",
    skip_pdf: bool = False,
    force_rerun: bool = False,
    briefing_variant: Optional[str] = None,
    override_today_lock: bool = False,
    report_title: Optional[str] = None,
    teaser_report: bool = False,
    # hospital
    radius_miles: Optional[int] = None,
    zip_code: Optional[str] = None,
    patient_perspective: bool = False,
    simplified: bool = False,
    obscure_competitors: bool = True,
    target_entity: Optional[str] = None,
    # hospital + practice
    practice_composite: bool = False,
    practice_roster: Optional[list] = None,
    physician_composite: bool = False,
    physician_roster: Optional[dict] = None,
    service_line: Optional[str] = None,
    parent_system: Optional[str] = None,
    # practice
    practice_profile: Optional[str] = None,
    confirmed_siblings: Optional[list] = None,
    org_name: Optional[str] = None,
    anchor_listing: Optional[dict] = None,
    extra_evidence: str = "",
    # community health
    fqhc_intake: Optional[dict] = None,
    site_roster: Optional[list] = None,
    # headless bulk scoring: compute, don't persist a History row
    skip_db_save: bool = False,
) -> AnalysisResult:
    """Run one individual report (Deep Diagnostic / Community Health) for `entity_type`
    in {hospital, practice, service_line, community_health}."""
    if entity_type not in _ADAPTERS:
        raise ValueError(f"Unknown individual report entity_type: {entity_type!r}")
    adapter = _ADAPTERS[entity_type]()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    from rich.console import Console
    console = Console(force_terminal=True, stderr=True)

    # ── 1. setup ──────────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "starting", "text": f"Starting {adapter.label}"})
    init_db()
    _loc_key = f"{city}, {state}"

    # ── 2. cache (same-day lock always; 30-day unless force_rerun) ───────────
    if not override_today_lock:
        from .db import get_recent_run
        _today = get_recent_run(entity_name, _loc_key, days=0, entity_type=adapter.type_key, aggregate=aggregate)
        if _today:
            emit({"type": "phase", "name": "cached", "text": f"Returning today's cached result for {entity_name}"})
            return AnalysisResult.model_validate_json(_today["result_json"])
        if not force_rerun:
            _cached = get_recent_run(entity_name, _loc_key, days=30, entity_type=adapter.type_key, aggregate=aggregate)
            if _cached:
                emit({"type": "phase", "name": "cached", "text": f"Returning cached result for {entity_name}"})
                return AnalysisResult.model_validate_json(_cached["result_json"])

    client = _get_client()
    run_id = str(uuid.uuid4())
    ctx = _Ctx(
        entity_type=entity_type, entity_name=entity_name, city=city, state=state, specialty=specialty,
        aggregate=aggregate, output_dir=output_dir, emit=emit, console=console, client=client, run_id=run_id,
        brand=brand, skip_pdf=skip_pdf, force_rerun=force_rerun, briefing_variant=briefing_variant,
        override_today_lock=override_today_lock, report_title=report_title, teaser_report=teaser_report,
        radius_miles=radius_miles, zip_code=zip_code, patient_perspective=patient_perspective,
        simplified=simplified, obscure_competitors=obscure_competitors, target_entity=target_entity,
        practice_composite=practice_composite, practice_roster=practice_roster,
        physician_composite=physician_composite, physician_roster=physician_roster,
        service_line=service_line, parent_system=parent_system, practice_profile=practice_profile,
        confirmed_siblings=confirmed_siblings, org_name=org_name, anchor_listing=anchor_listing,
        extra_evidence=extra_evidence, fqhc_intake=fqhc_intake, site_roster=site_roster,
    )

    # ── 3. evidence ───────────────────────────────────────────────────────────
    adapter.evidence(ctx)

    # ── 4. prompt ─────────────────────────────────────────────────────────────
    system_prompt, user_prompt = adapter.prompt(ctx)

    # ── 5. narrative ──────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "generating", "text": f"Generating {adapter.label}"})
    ctx.report_markdown = _stream_narrative(client, system_prompt, user_prompt, emit, console)

    # ── 6. structured extraction ──────────────────────────────────────────────
    emit({"type": "phase", "name": "structured", "text": "Extracting structured data"})
    tool, tool_name, max_tokens, extraction_prompt = adapter.extraction(ctx)
    structured = _run_structured_extraction(client, console, tool, tool_name, max_tokens, extraction_prompt, adapter.label)
    ctx.structured = structured

    # ── 7. build + score ──────────────────────────────────────────────────────
    rankings = adapter.build_and_score(ctx, structured)

    # ── 8. assemble ───────────────────────────────────────────────────────────
    _verdict = _clean(structured.get("ai_visibility_verdict", ""))
    _top_rec = _clean(structured.get("top_recommendation", ""))
    _warn_snake_case(_verdict, context=f"{adapter.type_key}/ai_visibility_verdict")
    _warn_snake_case(_top_rec, context=f"{adapter.type_key}/top_recommendation")
    common = dict(
        run_id=run_id,
        location=_loc_key,
        specialty=specialty,
        aggregate=aggregate,
        teaser_report=teaser_report,
        individual_report=True,
        entity_name=entity_name,
        report_title=ctx.report_title,
        generated_at=date.today(),
        data_collected_at=date.today(),
        weighting_profile=ctx.run_profile,
        market_overview=_clean(structured.get("market_overview", "")),
        ai_visibility_verdict=_verdict,
        coverage_note=adapter.coverage_note(ctx),
        top_recommendation=_top_rec,
        practical_advice=adapter.practical_advice(ctx, structured),
        improvement_sections=adapter.improvement_sections(ctx, structured),
        disclaimer=adapter.disclaimer(ctx),
        rankings=rankings,
        report_markdown=ctx.report_markdown,
    )
    result = AnalysisResult(**{**common, **adapter.extra_fields(ctx)})
    adapter.post_assemble(ctx, result)        # composites / MQCR battery (may rescore)

    # ── 9. markdown ───────────────────────────────────────────────────────────
    stem = adapter.stem(ctx)
    report_path = output_dir / f"{stem}.md"
    report_path.write_text(ctx.report_markdown, encoding="utf-8")
    result.md_path = str(report_path)
    console.print(f"[green]✓[/green] Report saved → [dim]{report_path}[/dim]")

    # ── 10. sources ───────────────────────────────────────────────────────────
    result.sources_consulted = pop_last_sources() + adapter.extra_sources(ctx, result)
    result.web_search_used = last_web_search_used()

    # ── 11. truncation gate (after any post-assemble rescoring) ───────────────
    if result.rankings and all(p.ai_visibility_score is None for p in result.rankings):
        _failure_msg = (
            f"Collection was too incomplete to score this {adapter.gate_noun} — "
            "fewer than half the pillar weights were populated. "
            "No prospect-facing PDF was produced. Review the markdown report and rerun."
        )
        emit({"type": "error", "message": _failure_msg})
        console.print(f"[yellow]⚠[/yellow] {_failure_msg}")
        skip_pdf = True

    # ── 12. plain-language condense ───────────────────────────────────────────
    from .plain import condense as _condense
    _condense(result, console=console)

    # ── 13. PDF (render errors propagate to the job) ──────────────────────────
    if skip_pdf:
        console.print("[dim]skip_pdf=True — PDF rendering skipped[/dim]")
    else:
        emit({"type": "phase", "name": "pdf", "text": "Rendering PDF"})
        pdf_path = output_dir / f"{stem}.pdf"
        with console.status("[bold dark_sea_green4]Rendering PDF…[/bold dark_sea_green4]"):
            adapter.render_pdf(ctx, result, pdf_path)
        console.print(f"[green]✓[/green] PDF saved    → [dim]{pdf_path}[/dim]")
        result.pdf_path = str(pdf_path)

    # ── 14. save ──────────────────────────────────────────────────────────────
    if not skip_db_save:
        _save_to_db(result)
        adapter.save_extras(ctx, result)
        _hosp._save_reputation_rows(result)

    # ── 15. briefing ──────────────────────────────────────────────────────────
    if briefing_variant and not skip_pdf:
        _hosp._run_briefing(result, briefing_variant, output_dir, stem, emit, console)

    # ── 16. done ──────────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "done_item", "text": "Complete"})
    return result
