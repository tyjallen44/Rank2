"""Network Pulse — analysis pipeline for multi-state hospital networks.

Two public entry points:
  extract_roster_from_url(url) → dict
  analyze_network(...)         → NetworkResult
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import anthropic

from .models import NetworkFacility, NetworkResult
from . import network_scoring
from .network_prompts import (
    build_roster_extraction_prompt,
    build_network_analysis_prompt,
    build_discovery_prompt,
    _ROSTER_TOOL,
    _ANALYSIS_TOOL,
    _DISCOVERY_TOOL,
)
from .db import get_connection, init_db, get_recent_network_run

client = anthropic.Anthropic()

_MODEL = "claude-opus-4-8"


# ─────────────────────────────────────────────────────────────────────────────
# Roster extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_roster_from_url(url: str) -> dict:
    """Load a network locations page and extract the hospital roster via Claude.

    Uses Playwright to load the URL (waits for networkidle, 30s timeout),
    then extracts page text and a content snippet for Claude to parse.

    Returns:
        {
            "facilities": [{"name": str, "city": str, "state": str, "beds": int|None}, ...],
            "network_name": str,
            "total_found": int,
        }
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        # Plain text is cleaner for facility parsing
        body_text = page.inner_text("body")
        # Also grab raw HTML for structure hints (first 80k chars)
        html_content = page.content()[:80000]
        browser.close()

    # Combine: prefer the plain text, fall back to HTML if text is sparse
    content = body_text if len(body_text) > 500 else html_content

    system_prompt, user_prompt = build_roster_extraction_prompt(content, url)

    response = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        tools=[_ROSTER_TOOL],
        tool_choice={"type": "tool", "name": "submit_hospital_roster"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_hospital_roster":
            data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            return {
                "facilities": data.get("facilities", []),
                "network_name": data.get("network_name", ""),
                "total_found": data.get("total_found", 0),
            }

    return {"facilities": [], "network_name": "", "total_found": 0}


def discover_hospitals_by_name(
    network_name: str,
    hq_location: str = "",
    facility_type: str = "hospital",
) -> dict:
    """Enumerate all facilities owned by a named healthcare network.

    Runs Claude and Gemini in parallel, then blends the results for maximum
    completeness. Falls back to Claude-only if GEMINI_API_KEY is not set.

    Returns:
        {
            "facilities": [{"name": str, "city": str, "state": str, "beds": int|None}, ...],
            "network_canonical_name": str,
            "total_found": int,
            "confidence_note": str,
        }
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    system_prompt, user_prompt = build_discovery_prompt(network_name, hq_location, facility_type)

    def _ask_claude() -> dict:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=8192,
            tools=[_DISCOVERY_TOOL],
            tool_choice={"type": "tool", "name": "submit_hospital_roster"},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_hospital_roster":
                d = block.input if isinstance(block.input, dict) else json.loads(block.input)
                return d
        return {}

    def _ask_gemini() -> dict:
        return _discover_via_gemini(system_prompt, user_prompt)

    gemini_key = _gemini_api_key()
    if gemini_key:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_claude  = pool.submit(_ask_claude)
            f_gemini  = pool.submit(_ask_gemini)
            claude_raw  = f_claude.result()
            gemini_raw  = f_gemini.result()
        blended = _blend_rosters(
            network_name,
            claude_raw.get("facilities", []),
            gemini_raw.get("facilities", []),
            claude_raw.get("network_canonical_name") or network_name,
        )
        return blended
    else:
        data = _ask_claude()
        return {
            "facilities": data.get("facilities", []),
            "network_canonical_name": data.get("network_canonical_name", network_name),
            "total_found": data.get("total_found", 0),
            "confidence_note": data.get("confidence_note", "Claude only — set GEMINI_API_KEY for dual-source verification."),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Network analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_network(
    network_name: str,
    hq_location: str,
    source_url: str,
    facilities: list[dict],
    facility_type: str = "hospital",
    brand: str = "original",
    on_event: Optional[Callable] = None,
    ignore_cache: bool = False,
    teaser: bool = False,
) -> NetworkResult:
    """Run a Network AI Visibility analysis for a multi-state healthcare network.

    Args:
        network_name:   Network display name (e.g. "Atrium Health" or "SCA Health").
        hq_location:    Headquarters city/state (e.g. "Charlotte, NC").
        source_url:     URL of the roster source page.
        facilities:     List of facility dicts: {name, city, state, beds?}.
        facility_type:  "hospital"|"asc"|"urgent_care"|"imaging"|"behavioral_health"|"other"
        brand:          Brand config key (passed to PDF renderer).
        on_event:       Optional callback receiving event dicts.

    Returns:
        NetworkResult with all fields populated and pdf_path set.
    """
    from .network_prompts import get_facility_config
    cfg = get_facility_config(facility_type)
    init_db()

    def emit(event: dict) -> None:
        if on_event:
            on_event(event)

    # ── 30-day cache ─────────────────────────────────────────────────────────
    if not ignore_cache:
        cached = get_recent_network_run(network_name, days=30)
        if cached:
            emit({"type": "phase", "name": "analyzing",
                  "text": f"Using recent cached result for {network_name}"})
            emit({"type": "phase", "name": "pdf", "text": "Serving cached PDF"})
            emit({"type": "phase", "name": "saving", "text": "Done"})
            return NetworkResult.model_validate_json(cached["result_json"])

    run_id = str(uuid.uuid4())

    # ── Phase: external quality data ─────────────────────────────────────────
    quality_cols = cfg.get("quality_cols", ["google"])
    if len(quality_cols) > 1:
        data_desc = "Google ratings, CMS stars, and Leapfrog grades"
    else:
        data_desc = "Google ratings"
    emit({"type": "phase", "name": "extracting",
          "text": f"Fetching {data_desc} for {len(facilities)} {cfg['plural']}"})
    facility_data = _fetch_facility_data(facilities, facility_type=facility_type)

    # ── Phase: analyzing ─────────────────────────────────────────────────────
    emit({"type": "phase", "name": "analyzing",
          "text": f"Analyzing AI visibility for {network_name}"})

    system_prompt, user_prompt = build_network_analysis_prompt(
        network_name=network_name,
        hq_location=hq_location,
        facilities=facilities,
        source_url=source_url,
        facility_data=facility_data,
        facility_type=facility_type,
    )

    # 16K is the safe non-streaming ceiling for claude-opus-4-8 (128K max, but
    # larger non-streaming calls risk SDK HTTP timeouts). A big network (e.g. 99
    # hospitals) needs well over 8K to fit every facility_assessment — at 8K the
    # tool_use JSON truncated, facility_assessments came back empty, and the
    # facility scorecard silently disappeared.
    response = client.messages.create(
        model=_MODEL,
        max_tokens=16000,
        tools=[_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_network_result"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if response.stop_reason == "max_tokens":
        import sys as _sys
        print(
            f"[network_analyzer] WARNING: analysis output hit max_tokens with "
            f"{len(facilities)} facilities — facility scorecard may be truncated.",
            file=_sys.stderr,
        )

    raw: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_network_result":
            raw = block.input if isinstance(block.input, dict) else json.loads(block.input)
            break

    # ── Headline four-pillar score (shared canonical, synced with Market) ─────
    # The network's headline is the SAME four-pillar Pulse Score the Hospital
    # Market report gives this system. Read the canonical store; on a miss, score
    # the system with the market's per-entity evaluator and seed the store.
    from .db import get_entity_score, upsert_entity_score
    from . import scoring as _sc
    _canon = None if ignore_cache else get_entity_score(network_name, hq_location, days=30)
    if _canon and _canon.get("pulse_score") is not None:
        pulse = _canon["pulse_score"]
        tier_scores = _canon.get("tier_scores") or {}
        ai_says = _canon.get("ai_says") or ""
        weighting_profile = "procedural"
    else:
        emit({"type": "phase", "name": "scoring",
              "text": f"Scoring {network_name} on the four-pillar rubric"})
        pulse, tier_scores, ai_says, weighting_profile = _entity_pulse_score(
            network_name, hq_location, brand=brand, emit=emit, force=ignore_cache
        )
        if pulse is not None:
            _code, _band = _sc.grade_from_score(pulse)
            upsert_entity_score(network_name, hq_location, pulse, tier_scores,
                                overall_rating=_code, band_label=_band, ai_says=ai_says,
                                source="network", run_id=run_id, overwrite=ignore_cache,
                                weighting_profile=weighting_profile or "procedural")
    quartile, quartile_band = _sc.grade_from_score(pulse)

    # Qualitative 3-dimension reads are kept only as optional commentary.
    brand_score    = raw.get("brand_visibility_score")
    market_score   = raw.get("market_coverage_score")
    accuracy_score = raw.get("information_accuracy_score")

    # ── Parse facilities (roster only — no competing composite score) ─────────
    facility_objects: list[NetworkFacility] = []
    for fa in raw.get("facility_assessments", []):
        if not isinstance(fa, dict):
            continue
        fac_name = fa.get("name", "")
        fd       = facility_data.get(fac_name.lower(), {})
        facility_objects.append(NetworkFacility(
            name=fac_name,
            city=fa.get("city", ""),
            state=fa.get("state", ""),
            beds=next(
                (f.get("beds") for f in facilities
                 if f.get("name", "").lower() == fac_name.lower()),
                None,
            ),
            surfaced_for_local=fa.get("surfaced_for_local"),
            google_rating=fd.get("google_rating"),
            google_review_count=fd.get("google_review_count"),
            cms_star_rating=fd.get("cms_star_rating"),
            leapfrog_grade=fd.get("leapfrog_grade"),
        ))
    # Roster order is geographic (per-facility scores are no longer shown).
    facility_objects.sort(key=lambda f: ((f.state or ""), (f.city or ""), (f.name or "")))

    states_covered = sorted({f.get("state", "") for f in facilities if f.get("state")})

    result = NetworkResult(
        run_id=run_id,
        network_name=network_name,
        network_canonical_name=raw.get("network_canonical_name") or network_name,
        hq_location=hq_location,
        source_url=source_url,
        facility_type=facility_type,
        total_hospitals=len(facilities),
        states_covered=states_covered,
        generated_at=date.today(),
        data_collected_at=date.today(),
        ai_visibility_score=pulse,
        brand_visibility_score=brand_score,
        market_coverage_score=market_score,
        information_accuracy_score=accuracy_score,
        grade=quartile,
        grade_band=quartile_band,
        tier_scores=tier_scores,
        weighting_profile=weighting_profile,
        ai_says=ai_says,
        executive_summary=raw.get("executive_summary", ""),
        brand_visibility_narrative=raw.get("brand_visibility_narrative", ""),
        market_coverage_narrative=raw.get("market_coverage_narrative", ""),
        information_accuracy_narrative=raw.get("information_accuracy_narrative", ""),
        strategic_recommendations=[
            r for r in raw.get("strategic_recommendations", [])
            if isinstance(r, str)
        ],
        top_markets=[m for m in raw.get("top_markets", []) if isinstance(m, str)],
        gap_markets=[m for m in raw.get("gap_markets", []) if isinstance(m, str)],
        facilities=facility_objects,
    )

    # ── Phase: pdf ───────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "pdf",
          "text": "Rendering Hospital Network PDF"})
    try:
        from .network_pdf import render_network_pdf
        output_dir = Path("reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        from .strings import titlecase_filename
        slug = _slug(network_name)
        _ts = datetime.utcnow().strftime("%y%m%d-%H%M")
        pdf_filename = titlecase_filename(f"{slug}-hospital-network-{_ts}") + ".pdf"
        pdf_path = output_dir / pdf_filename
        render_network_pdf(result, str(pdf_path), brand=brand)
        result.pdf_path = str(pdf_path)
        if teaser:
            teaser_filename = titlecase_filename(f"{slug}-hospital-network-teaser-{_ts}") + ".pdf"
            teaser_path = output_dir / teaser_filename
            render_network_pdf(result, str(teaser_path), brand=brand, teaser=True)
            result.teaser_pdf_path = str(teaser_path)
    except Exception as exc:
        emit({"type": "text", "text": f"\n⚠ PDF render failed: {exc}\n"})

    # ── Phase: saving ────────────────────────────────────────────────────────
    emit({"type": "phase", "name": "saving", "text": "Saving to database"})
    _save_network_run(result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _split_location(loc: str) -> tuple[str, str]:
    """'Chicago, IL' → ('Chicago', 'IL'). Falls back to (loc, '')."""
    parts = [p.strip() for p in (loc or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (loc or "").strip(), ""


def _entity_pulse_score(entity_name: str, location: str, brand: str = "original",
                        emit: Optional[Callable] = None, force: bool = False):
    """Score one system on the four-pillar rubric using the SAME per-entity
    evaluator the Hospital Market report uses. Returns
    (pulse_score, tier_scores dict, ai_says, weighting_profile)."""
    from .analyzer import analyze_location
    city, state = _split_location(location)
    try:
        res = analyze_location(
            city=city, state=state, entity_name=entity_name,
            individual_report=True, entity_type="hospital",
            skip_pdf=True, on_event=emit, brand=brand,
            force_rerun=force, override_today_lock=force,
        )
    except Exception as exc:
        import sys as _sys
        print(f"[network_analyzer] four-pillar scoring failed for "
              f"{entity_name}: {exc}", file=_sys.stderr)
        return None, {}, "", "procedural"
    prov = res.rankings[0] if res.rankings else None
    if prov is None:
        return None, {}, "", "procedural"
    tiers = prov.tier_scores.as_dict() if hasattr(prov.tier_scores, "as_dict") else {}
    return (prov.ai_visibility_score, tiers,
            getattr(prov, "ai_says", "") or "", res.weighting_profile or "procedural")


def _gemini_api_key() -> str | None:
    import os
    return os.environ.get("GEMINI_API_KEY") or None


def _discover_via_gemini(system_prompt: str, user_prompt: str) -> dict:
    """Call Gemini 2.5 Flash with the same roster-discovery prompt and tool schema."""
    import os
    import httpx

    key = _gemini_api_key()
    if not key:
        return {}

    # Convert Anthropic tool schema → Gemini function declaration format
    tool_schema = _DISCOVERY_TOOL["input_schema"]
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "tools": [{"function_declarations": [{
            "name": _DISCOVERY_TOOL["name"],
            "description": _DISCOVERY_TOOL["description"],
            "parameters": tool_schema,
        }]}],
        "tool_config": {
            "function_calling_config": {
                "mode": "ANY",
                "allowed_function_names": [_DISCOVERY_TOOL["name"]],
            }
        },
        "generation_config": {"max_output_tokens": 8192},
    }

    try:
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            fn = part.get("functionCall", {})
            if fn.get("name") == _DISCOVERY_TOOL["name"]:
                args = fn.get("args", {})
                return args
    except Exception:
        pass
    return {}


def _normalize_tokens(name: str) -> frozenset[str]:
    """Return significant lowercase tokens from a hospital name for fuzzy matching."""
    import re
    _STOP = frozenset({
        "hospital", "medical", "center", "centre", "health", "system", "healthcare",
        "regional", "memorial", "general", "community", "care", "clinic", "services",
        "of", "the", "at", "and", "for", "in", "a", "an", "&", "-",
    })
    tokens = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    return frozenset(t for t in tokens if t not in _STOP and len(t) > 1)


def _is_same_facility(a: dict, b: dict) -> bool:
    """True if two facility dicts likely refer to the same hospital."""
    if (a.get("state") or "").upper() != (b.get("state") or "").upper():
        return False
    tok_a = _normalize_tokens(a.get("name", ""))
    tok_b = _normalize_tokens(b.get("name", ""))
    if not tok_a or not tok_b:
        return False
    overlap = len(tok_a & tok_b) / min(len(tok_a), len(tok_b))
    return overlap >= 0.5


def _blend_rosters(
    network_name: str,
    claude_list: list[dict],
    gemini_list: list[dict],
    canonical_name: str,
) -> dict:
    """Merge Claude and Gemini facility lists, deduplicating by fuzzy name + state.

    Hospitals confirmed by both sources are marked high-confidence.
    Hospitals from only one source are included but flagged.
    Returns the same structure as discover_hospitals_by_name.
    """
    merged: list[dict] = []
    both_count  = 0
    claude_only = 0
    gemini_only = 0

    # Start with Claude's list; tag each with source
    for fac in claude_list:
        merged.append({**fac, "_sources": {"claude"}})

    # Walk Gemini list: find match in merged or append
    for gfac in gemini_list:
        matched = False
        for mfac in merged:
            if _is_same_facility(gfac, mfac):
                mfac["_sources"].add("gemini")
                # Prefer non-None beds value
                if mfac.get("beds") is None and gfac.get("beds") is not None:
                    mfac["beds"] = gfac["beds"]
                matched = True
                break
        if not matched:
            merged.append({**gfac, "_sources": {"gemini"}})

    # Tally sources and strip internal tag
    facilities = []
    for fac in merged:
        sources = fac.pop("_sources", set())
        if "claude" in sources and "gemini" in sources:
            both_count += 1
        elif "claude" in sources:
            claude_only += 1
        else:
            gemini_only += 1
        facilities.append({k: v for k, v in fac.items()
                            if k in ("name", "city", "state", "beds")})

    total = len(facilities)
    note = (
        f"Dual-source roster: {both_count} hospitals confirmed by both Claude and Gemini"
        + (f", {claude_only} from Claude only" if claude_only else "")
        + (f", {gemini_only} from Gemini only" if gemini_only else "")
        + f". Total: {total} facilities."
    )

    return {
        "facilities": facilities,
        "network_canonical_name": canonical_name,
        "total_found": total,
        "confidence_note": note,
    }


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _fetch_facility_data(
    facilities: list[dict],
    facility_type: str = "hospital",
) -> dict[str, dict]:
    """Fetch external quality data for all facilities.

    Always fetches Google ratings.
    CMS star ratings and Leapfrog grades are fetched only for hospital-type networks.

    Returns dict keyed by lowercase facility name.

    Cloud Run safety: each source uses its own pool with a hard timeout so that
    a hung Playwright browser or slow API call never blocks the entire pipeline.
    Leapfrog uses at most 2 concurrent Chromium instances to stay within Cloud Run
    memory limits.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
    from .data.places import fetch_google_rating
    from .network_prompts import get_facility_config

    cfg = get_facility_config(facility_type)
    fetch_cms = "cms" in cfg.get("quality_cols", [])
    fetch_lf  = "leapfrog" in cfg.get("quality_cols", [])

    results: dict[str, dict] = {
        fac.get("name", "").lower(): {
            "google_rating": None, "google_review_count": None,
            "cms_star_rating": None, "leapfrog_grade": None,
        }
        for fac in facilities
    }

    # ── Google (fast; 8 workers; 60s total timeout) ───────────────────────────
    def _google_one(fac: dict) -> tuple[str, float | None, int | None]:
        key = fac.get("name", "").lower()
        try:
            read = fetch_google_rating(fac.get("name", ""), fac.get("city"), fac.get("state"))
            if read.verified:
                return key, read.rating, read.review_count
        except Exception:
            pass
        return key, None, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        g_futures = {pool.submit(_google_one, f): f for f in facilities}
        try:
            for fut in as_completed(g_futures, timeout=60):
                try:
                    key, rating, count = fut.result()
                    results[key]["google_rating"]       = rating
                    results[key]["google_review_count"] = count
                except Exception:
                    pass
        except FuturesTimeout:
            pass  # collect whatever completed; missing ones stay None

    # ── Leapfrog (Playwright; max 2 Chromium instances; 150s total timeout) ──
    if fetch_lf:
        from .data.leapfrog import fetch_leapfrog_grade

        def _leapfrog_one(fac: dict) -> tuple[str, str | None]:
            key = fac.get("name", "").lower()
            try:
                grade = fetch_leapfrog_grade(
                    fac.get("name", ""), fac.get("city", ""), fac.get("state", "")
                )
                return key, grade
            except Exception:
                return key, None

        with ThreadPoolExecutor(max_workers=2) as lf_pool:
            lf_futures = {lf_pool.submit(_leapfrog_one, f): f for f in facilities}
            try:
                for fut in as_completed(lf_futures, timeout=150):
                    try:
                        key, grade = fut.result()
                        results[key]["leapfrog_grade"] = grade
                    except Exception:
                        pass
            except FuturesTimeout:
                pass  # partial grades are fine; missing ones stay None

    # ── CMS (HTTP batched by state; 60s timeout) ─────────────────────────────
    if fetch_cms:
        from .data.cms import fetch_star_ratings_for_network
        with ThreadPoolExecutor(max_workers=1) as cms_pool:
            cms_future = cms_pool.submit(fetch_star_ratings_for_network, facilities)
            try:
                cms_map = cms_future.result(timeout=60)
                for key, star in cms_map.items():
                    if key in results:
                        results[key]["cms_star_rating"] = star
            except Exception:
                pass

    return results


def _save_network_run(result: NetworkResult) -> None:
    """Persist a NetworkResult to the network_runs table."""
    with get_connection() as con:
        con.execute(
            """INSERT INTO network_runs
               (run_id, network_name, hq_location, source_url, facility_type, total_hospitals,
                ai_visibility_score, grade, generated_at, result_json, pdf_path, teaser_pdf_path,
                user_role, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (run_id) DO UPDATE SET
                   ai_visibility_score = excluded.ai_visibility_score,
                   grade               = excluded.grade,
                   result_json         = excluded.result_json,
                   pdf_path            = excluded.pdf_path,
                   teaser_pdf_path     = excluded.teaser_pdf_path""",
            [
                result.run_id,
                result.network_name,
                result.hq_location,
                result.source_url,
                result.facility_type or "hospital",
                result.total_hospitals,
                result.ai_visibility_score,
                result.grade,
                result.generated_at.isoformat(),
                result.model_dump_json(),
                result.pdf_path,
                result.teaser_pdf_path,
                "admin",
                datetime.utcnow(),
            ],
        )
