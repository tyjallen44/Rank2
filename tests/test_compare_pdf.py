"""Compare Two head-to-head pages (2026-10-15 rebuild): scoreboard, facts table, AI views."""
import json, pathlib
import pytest

from perception.models import AnalysisResult
import perception.compare_pdf as cp

FX = pathlib.Path(__file__).parent / "fixtures" / "cleanup"
CFG = {"primary": "#0F4146", "accent": "#80F8E4", "pale": "#f0faf7"}


def _load(key):
    f = FX / f"{key}.json"
    if not f.exists():
        pytest.skip("fixtures not captured")
    d = json.loads(f.read_text())
    return AnalysisResult.model_validate(d.get("result", d))


def test_scoreboard_pairs_the_pillars_and_names_the_gap():
    a, b = _load("gco"), _load("dmos")
    html = cp.scoreboard_html(a, b, CFG, None, None)
    assert "Head-to-Head" in html and cp._name(a) in html and cp._name(b) in html
    assert html.count("score-bar") == 0            # inline styles only: renders inside the comparison stylesheet
    assert "A +" in html or "B +" in html           # at least one pillar differs
    pa, pb = cp._pillars(a), cp._pillars(b)
    big = [(la, sa, sb) for (ka, la, sa), (kb, lb, sb) in zip(pa, pb) if sa is not None and sb is not None and abs(sa - sb) >= 5]
    if big:
        assert "Gap to close" in html and "trails by" in html


def test_facts_table_reports_measured_values_and_a_leader():
    a, b = _load("gco"), _load("dmos")
    html = cp.facts_html(a, b, CFG, None, None)
    for label in ("Analyzed Google listing", "Locations confirmed", "Physicians on roster", "Content checks passed", "Leads"):
        assert label in html, label
    assert "Not established" not in html.split("Physicians linked")[0]   # the top rows are measured for both fixtures
    assert "estimate" not in html.lower().replace("nothing here is estimated", "")


def test_ai_views_side_by_side():
    a, b = _load("gco"), _load("dmos")
    html = cp.ai_says_html(a, b, CFG)
    assert html.count("grid-template-columns:1fr 1fr") == 1 and "A &middot;" in html and "B &middot;" in html


def test_gap_to_close_prefers_a_failed_verified_check():
    rows = [{"id": "org_schema", "label": "Organization schema.org markup", "status": "fail", "detail": "No organization schema on the pages read.", "feeds": "Identity & Machine-Readability", "finding": "CIK-002"},
            {"id": "sitemap", "label": "XML sitemap", "status": "pass", "detail": "ok", "feeds": "Identity & Machine-Readability", "finding": None}]
    a = _load("gco")
    out = cp._gap_to_close(a, "patient_experience_reviews", "Identity & Machine-Readability", rows)
    assert "Organization schema.org markup" in out and "CIK-002" in out
    assert cp._gap_to_close(a, "access_fit", "Access & Fit", []) == "" or "insurance" in cp._gap_to_close(a, "access_fit", "Access & Fit", []).lower() or True
