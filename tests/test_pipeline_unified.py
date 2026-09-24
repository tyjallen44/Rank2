"""Offline tests for the unified individual-report pipeline (perception/pipeline.py).

Every network / model / render step is monkeypatched: narrative streaming, the
structured-extraction client, Places + quality evidence, HRSA, the entity-score
sync, plain.condense, both PDF renderers and the briefing. The DB is real (rows
are cleaned up per test); the module is skipped if the DB is unreachable.

Run with: python -m pytest tests/test_pipeline_unified.py -v
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:
    pass


def _db_ok() -> bool:
    try:
        from perception.db import get_connection, init_db
        init_db()
        con = get_connection()
        con.execute("SELECT 1").fetchone()
        con.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="database not reachable")


# ── fakes ────────────────────────────────────────────────────────────────────

_TIERS = {"clinical_outcomes_safety": 70, "credentials_recognition": 65,
          "patient_experience_reviews": 60, "access_fit": 55}
_NULL_TIERS = {k: None for k in _TIERS}


def _ranking(name: str, tiers: dict, profile: str) -> dict:
    return {
        "rank": 1, "name": name, "website_url": None, "affiliation_type": "independent",
        "size_category": "community", "overall_rating": "", "weighting_profile": profile,
        "tier_scores": dict(tiers),
        "google_footprint": {"front_door": {"rating": None, "count": None, "verified": False},
                             "consistency": "", "gap_note": ""},
        "third_party_aggregate": {"note": ""},
        "key_strengths": [], "notable_weaknesses": [], "best_suited_for": "",
        "recommendation_summary": "", "consolidated_locations": [
            {"name": f"{name} - North", "overall_rating": ""},
            {"name": f"{name} - South", "overall_rating": ""},
        ],
        "patient_voice_summary": "", "accreditations": [], "cms_quality_highlights": "",
        "ai_says": "AI assistants mention it.", "leapfrog_grade": None, "cms_star_rating": None,
        "us_news_rankings": [], "trauma_level": None, "teaching_status": None,
        "hrsa_verification": {"directory_note": ""},
    }


def _structured(etype: str, name: str, null_scores: bool = False) -> dict:
    tiers = _NULL_TIERS if null_scores else _TIERS
    base = {
        "market_overview": "Overview.", "ai_visibility_verdict": "Verdict.",
        "top_recommendation": "Do the thing.", "practical_advice": [],
        "improvement_sections": [{"title": "Your Website", "description": "d", "items": ["fix schema"]}],
        "disclaimer": "",
    }
    if etype == "community_health":
        ps = {k: None for k in ("mqcr_score", "multilingual_score", "service_adjacent_score",
                                "eligibility_cost_accuracy", "site_service_completeness",
                                "experience_reputation", "institutional_signals")}
        if not null_scores:
            ps.update(service_adjacent_score=60, eligibility_cost_accuracy=70,
                      site_service_completeness=50, experience_reputation=65, institutional_signals=80)
        base.update(fqhc_pillar_scores=ps, fact_audit_rows=[], missed_queries=[],
                    rankings=[_ranking(name, tiers, "community_health")])
        return base
    profile = "practice_procedural" if etype == "practice" else "procedural"
    base.update(weighting_profile=profile,
                rankings=[_ranking(name, tiers, profile)])
    if etype == "practice":
        base.update(entity_resolution_pct=95.0, linkage_integrity_pct=90.0,
                    board_cert_unverifiable=False, key_person_flag=False)
    return base


class _FakeStream:
    def __init__(self, message):
        self._m = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._m


class _FakeClient:
    """messages.stream(...) returns a forced tool_use whose input is the structured dict."""

    def __init__(self, structured: dict):
        self.structured = structured
        self.calls: list[dict] = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        tool_name = kw["tool_choice"]["name"]
        block = SimpleNamespace(type="tool_use", name=tool_name, input=self.structured)
        return _FakeStream(SimpleNamespace(stop_reason="end_turn", content=[block]))


def _score_from_tiers(prov, profile):
    from perception import scoring
    prov.ai_visibility_score = scoring.composite_score(prov.tier_scores.as_dict(), profile)
    prov.overall_rating, _ = scoring.grade_from_score(prov.ai_visibility_score)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Patch every external step; returns a driver that runs the pipeline and
    records events / calls, and cleans up DB rows afterwards."""
    from perception import pipeline, analyzer, practice_analyzer, fqhc_analyzer
    from perception import plain, pdf, fqhc_pdf, briefing, briefing_pdf

    state = {"structured": {}, "pdf_calls": [], "briefing_calls": [], "run_ids": [],
             "battery": None, "quality": None}

    monkeypatch.setattr(pipeline, "_get_client", lambda: _FakeClient(state["structured"]))
    monkeypatch.setattr(pipeline, "_stream_narrative",
                        lambda client, sp, up, emit, console: "### Organization Overview\nNarrative.")

    def _evidence(name, city, st, etype=None):
        read = SimpleNamespace(formatted_address=None)
        return f"=== Evidence: {name} ===", read, state["quality"]
    monkeypatch.setattr(pipeline, "_gather_individual_evidence", _evidence)

    # Grounding without Places: score straight from the tier scores.
    monkeypatch.setattr(analyzer, "_ground_and_score",
                        lambda prov, city, st, gi, fi, do_system=False: _score_from_tiers(prov, prov.weighting_profile))

    def _ground_practice(prov, city, st, entity_resolution_pct, linkage_integrity_pct,
                         board_cert_unverifiable, roster_rep=None, pinned_anchor=None):
        from perception import practice_scoring, scoring
        score, applied, reason = practice_scoring.composite(
            prov.tier_scores.as_dict(), prov.weighting_profile or "practice_procedural",
            entity_resolution_pct=entity_resolution_pct, linkage_integrity_pct=linkage_integrity_pct,
            board_cert_unverifiable=board_cert_unverifiable)
        prov.ai_visibility_score = score
        prov.overall_rating, _ = scoring.grade_from_score(score)
        prov._score_ceiling_applied = applied
        prov._score_ceiling_reason = reason
    monkeypatch.setattr(practice_analyzer, "_ground_and_score_practice", _ground_practice)

    def _ground_fqhc(prov, city, st, pillar_scores):
        from perception import fqhc_scoring, scoring
        prov.ai_visibility_score = fqhc_scoring.composite(pillar_scores.as_dict())
        prov.overall_rating, _ = scoring.grade_from_score(prov.ai_visibility_score)
    monkeypatch.setattr(fqhc_analyzer, "_ground_and_score_fqhc", _ground_fqhc)

    monkeypatch.setattr(analyzer, "_sync_entity_scores", lambda *a, **k: None)
    monkeypatch.setattr(practice_analyzer, "_sync_practice_entity_score", lambda *a, **k: None)
    monkeypatch.setattr(fqhc_analyzer, "_lookup_hrsa", lambda name, city, st, emit, console: {"found": False})

    def _battery(result, run_id, name, city, st, hrsa, emit, console):
        if state["battery"] is not None:
            from perception import scoring
            result.fqhc_mqcr = state["battery"]
            result.rankings[0].ai_visibility_score = int(round(state["battery"] * 100))
            result.rankings[0].overall_rating, _ = scoring.grade_from_score(result.rankings[0].ai_visibility_score)
    monkeypatch.setattr(fqhc_analyzer, "_run_mqcr_battery", _battery)

    monkeypatch.setattr(plain, "condense", lambda result, **k: False)

    def _render(result, pdf_path, brand="original"):
        state["pdf_calls"].append(str(pdf_path))
    monkeypatch.setattr(pdf, "render_pdf", _render)
    monkeypatch.setattr(fqhc_pdf, "render_fqhc_pdf", _render)

    monkeypatch.setattr(briefing, "extract", lambda result, variant: {"variant": variant})

    def _render_briefing(br, path):
        state["briefing_calls"].append(path)
        return path
    monkeypatch.setattr(briefing_pdf, "render_briefing_pdf", _render_briefing)

    def run(etype, name="Pipeline Test Entity", null_scores=False, quality=None, battery=None, **kw):
        state["structured"] = _structured("practice" if etype == "service_line" else etype, name, null_scores)
        state["quality"] = quality
        state["battery"] = battery
        events: list[dict] = []
        kw.setdefault("override_today_lock", True)
        kw.setdefault("force_rerun", True)
        res = pipeline.run_individual(etype, name, "Testville", "NC", output_dir=tmp_path,
                                      on_event=events.append, **kw)
        state["run_ids"].append(res.run_id)
        return res, events

    yield SimpleNamespace(run=run, state=state, tmp_path=tmp_path)

    from perception.db import get_connection
    con = get_connection()
    for rid in state["run_ids"]:
        for table in ("ranked_providers", "fqhc_intake", "fqhc_fact_audit", "analysis_runs"):
            try:
                con.execute(f"DELETE FROM {table} WHERE run_id = ?", [rid])
            except Exception:
                pass
    con.close()


def _row(run_id: str, cols: str):
    from perception.db import get_connection
    con = get_connection()
    row = con.execute(f"SELECT {cols} FROM analysis_runs WHERE run_id = ?", [run_id]).fetchone()
    con.close()
    return row


# ── tests ────────────────────────────────────────────────────────────────────

def test_unknown_entity_type_rejected():
    from perception.pipeline import run_individual
    with pytest.raises(ValueError):
        run_individual("hospital_network", "X", "Y", "NC")


def test_hospital_run_persists_entity_type_and_completes(harness):
    q = {"cms_facility_id": "340030", "cms_name": "Pipeline Test Entity", "cms_star": 4, "leapfrog_grade": "A"}
    res, events = harness.run("hospital", quality=q, specialty=None)
    assert res.entity_type == "hospital"
    assert res.individual_report is True
    assert res.weighting_profile == "procedural"
    assert res.rankings and res.rankings[0].ai_visibility_score is not None
    # CMS/Leapfrog override anchored Outcomes & Safety and survived into the result
    from perception import scoring
    assert res.rankings[0].tier_scores.clinical_outcomes_safety == scoring.outcomes_band("A", 4)
    assert res.rankings[0].cms_star_rating == 4 and res.rankings[0].leapfrog_grade == "A"
    assert res.verified_quality == q
    assert any(s.get("domain") == "medicare.gov" for s in res.sources_consulted)
    assert events[-1] == {"type": "phase", "name": "done_item", "text": "Complete"}
    assert not any(e.get("type") == "error" for e in events)
    assert res.pdf_path and harness.state["pdf_calls"] == [res.pdf_path]
    assert res.md_path and os.path.exists(res.md_path)
    row = _row(res.run_id, "entity_type, individual_report, aggregate")
    assert row is not None and row[0] == "hospital" and row[1] is True and row[2] is False


def test_practice_run_fields_and_extras(harness):
    res, events = harness.run("practice", specialty="Sports Medicine", practice_composite=False)
    assert res.entity_type == "practice"
    assert res.weighting_profile == "practice_procedural"
    assert res.practice_profile == "practice_procedural"
    assert res.rubric_version == "practice-v1.0"
    assert res.coverage_note.startswith("Individual practice report")
    assert res.rankings[0].ai_visibility_score is not None
    assert events[-1]["name"] == "done_item"
    row = _row(res.run_id, "entity_type, practice_profile, rubric_version")
    assert row == ("practice", "practice_procedural", "practice-v1.0")


def test_service_line_uses_practice_adapter(harness):
    res, _ = harness.run("service_line", specialty="Orthopedics", service_line="Orthopedics",
                         parent_system="Test Health")
    assert res.entity_type == "practice"
    assert res.service_line == "Orthopedics" and res.parent_system == "Test Health"
    assert _row(res.run_id, "entity_type, service_line") == ("practice", "Orthopedics")


def test_community_health_run(harness):
    res, events = harness.run("community_health", battery=0.7)
    assert res.entity_type == "community_health"
    assert res.weighting_profile == "community_health"
    assert res.rubric_version == "community-health-v1.0"
    assert res.fqhc_intake and res.fqhc_intake.get("sliding_fee_scale") is True   # synthesized intake
    assert res.fqhc_pillar_scores is not None and res.fqhc_mqcr == 0.7
    assert "HRSA" in res.disclaimer
    assert events[-1]["name"] == "done_item"
    assert not any(e.get("type") == "done" for e in events)          # no bare 'done' stream-ender
    assert res.pdf_path and harness.state["pdf_calls"] == [res.pdf_path]
    row = _row(res.run_id, "entity_type, edition")
    assert row == ("community_health", "community_health")


def test_truncation_gate_blocks_pdf_when_unscored(harness):
    res, events = harness.run("hospital", null_scores=True)
    assert all(p.ai_visibility_score is None for p in res.rankings)
    errs = [e for e in events if e.get("type") == "error"]
    assert len(errs) == 1 and "too incomplete to score this entity" in errs[0]["message"]
    assert res.pdf_path is None and harness.state["pdf_calls"] == []
    assert res.md_path                                   # markdown still written
    assert _row(res.run_id, "entity_type") == ("hospital",)   # still saved


def test_fqhc_gate_runs_after_battery(harness):
    # Extraction yields no pillar scores (composite None) — the battery then restores a score.
    res, events = harness.run("community_health", null_scores=True, battery=0.6)
    assert res.rankings[0].ai_visibility_score == 60
    assert not any(e.get("type") == "error" for e in events)
    assert res.pdf_path and harness.state["pdf_calls"] == [res.pdf_path]


def test_fqhc_gate_fires_when_battery_cannot_rescue(harness):
    res, events = harness.run("community_health", null_scores=True, battery=None)
    assert res.rankings[0].ai_visibility_score is None
    errs = [e for e in events if e.get("type") == "error"]
    assert len(errs) == 1 and "health center" in errs[0]["message"]
    assert res.pdf_path is None


def test_briefing_gated_on_variant_and_pdf(harness):
    res, events = harness.run("practice", briefing_variant="sales")
    assert harness.state["briefing_calls"] and res.briefing_pdf_path
    assert res.briefing_variant == "sales"
    assert any(e.get("type") == "briefing_ready" for e in events)
    harness.state["briefing_calls"].clear()
    res2, events2 = harness.run("practice", briefing_variant="sales", skip_pdf=True)
    assert harness.state["briefing_calls"] == [] and res2.briefing_pdf_path is None
    assert not any(e.get("type") in ("briefing_ready", "briefing_skipped") for e in events2)


def test_cache_same_day_lock_keys_on_aggregate(harness):
    res, _ = harness.run("hospital", aggregate=False)
    # Same-day lock (override off): the just-saved single run is served back.
    res2, events2 = harness.run("hospital", aggregate=False, override_today_lock=False, force_rerun=True)
    assert res2.run_id == res.run_id
    assert any(e.get("name") == "cached" for e in events2)
    # An aggregate request must NOT share the single run's slot.
    res3, events3 = harness.run("hospital", aggregate=True, override_today_lock=False, force_rerun=True)
    assert res3.run_id != res.run_id
    assert not any(e.get("name") == "cached" for e in events3)


def test_server_dispatch_honours_pipeline_flag(monkeypatch):
    """_run_type_analyzer routes to pipeline.run_individual unless PULSE_PIPELINE=legacy."""
    import importlib
    import server as _server
    from perception import pipeline, analyzer, practice_analyzer, fqhc_analyzer

    seen = {}
    monkeypatch.setattr(pipeline, "run_individual", lambda *a, **k: seen.setdefault("unified", (a, k)))
    monkeypatch.setattr(analyzer, "analyze_location", lambda **k: seen.setdefault("legacy_hospital", k))
    monkeypatch.setattr(practice_analyzer, "analyze_practice", lambda **k: seen.setdefault("legacy_practice", k))
    monkeypatch.setattr(fqhc_analyzer, "analyze_fqhc", lambda **k: seen.setdefault("legacy_fqhc", k))
    job = {"brand": "original", "practice_facts": None}

    monkeypatch.setenv("PULSE_PIPELINE", "unified")
    _server._run_type_analyzer("hospital", job, "E", "C", "NC", None, False, None, lambda e: None)
    assert "unified" in seen and seen["unified"][0][0] == "hospital"
    assert seen["unified"][1]["skip_pdf"] is False and seen["unified"][1]["teaser_report"] is False

    seen.clear()
    monkeypatch.setenv("PULSE_PIPELINE", "legacy")
    _server._run_type_analyzer("hospital", job, "E", "C", "NC", None, False, None, lambda e: None)
    _server._run_type_analyzer("practice", job, "E", "C", "NC", "Ortho", True, None, lambda e: None)
    _server._run_type_analyzer("community_health", job, "E", "C", "NC", None, False, None, lambda e: None)
    assert set(seen) == {"legacy_hospital", "legacy_practice", "legacy_fqhc"}
    assert seen["legacy_hospital"]["individual_report"] is True
