"""Tier 1/2 regression: verify existing pipelines are intact after composite rollback.

Structural/import-only tests — no server or Claude API required.
Run with: python -m pytest tests/test_regression.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect
import pytest


# ── pdf.py surface area unchanged ─────────────────────────────────────────────

def test_pdf_original_functions_present():
    from perception import pdf
    for fn in ("render_pdf", "render_comparison_pdf"):
        assert callable(getattr(pdf, fn, None)), f"pdf.{fn} missing"

def test_pdf_composite_function_removed():
    from perception import pdf
    assert not hasattr(pdf, "render_composite_pdf"), \
        "render_composite_pdf should have been removed in composite rollback"

def test_pdf_render_pdf_signature_unchanged():
    from perception.pdf import render_pdf
    sig = inspect.signature(render_pdf)
    params = list(sig.parameters)
    assert "result" in params
    assert "pdf_path" in params

def test_pdf_render_comparison_signature_unchanged():
    from perception.pdf import render_comparison_pdf
    sig = inspect.signature(render_comparison_pdf)
    params = list(sig.parameters)
    assert "result_a" in params
    assert "result_b" in params

def test_pdf_practice_reputation_table_present():
    from perception import pdf
    assert callable(getattr(pdf, "_practice_reputation_table_html", None)), \
        "pdf._practice_reputation_table_html missing"


# ── models.py unchanged for existing fields ───────────────────────────────────

def test_analysis_result_existing_fields():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(
        run_id="test-001",
        location="Mobile, Alabama",
        generated_at=date.today(),
    )
    assert r.aggregate is False
    assert r.teaser_report is False
    assert r.individual_report is False

def test_analysis_result_no_composite_mode_field():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(run_id="x", location="y", generated_at=date.today())
    assert not hasattr(r, "composite_mode"), \
        "composite_mode should have been removed from AnalysisResult"

def test_analysis_result_practice_composite_rows_default():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(run_id="x", location="y", generated_at=date.today())
    assert r.practice_composite_rows == []

def test_analysis_result_serializes_cleanly():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(run_id="x", location="y", generated_at=date.today())
    d = r.model_dump()
    assert "practice_composite_rows" in d
    assert d["practice_composite_rows"] == []


# ── composite files removed ────────────────────────────────────────────────────

def test_composite_modules_removed():
    import importlib
    for mod in (
        "perception.composite_analyzer",
        "perception.composite_config",
        "perception.composite_models",
        "perception.composite_scoring",
    ):
        spec = importlib.util.find_spec(mod)
        assert spec is None, f"{mod} should have been deleted"


# ── new practice modules importable ───────────────────────────────────────────

def test_practice_discovery_importable():
    from perception.practice_discovery import discover_practices
    assert callable(discover_practices)

def test_practice_reputation_importable():
    from perception.practice_reputation import collect_platform_data, save_practice_reputation
    assert callable(collect_platform_data)
    assert callable(save_practice_reputation)


# ── db.py schema after init ───────────────────────────────────────────────────

def _run_init_db_on_temp():
    import duckdb
    import tempfile
    from perception import db as _db
    from perception.db import init_db

    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)

    orig_path = _db.settings.db_path
    try:
        _db.settings.db_path = path
        init_db()
        con = duckdb.connect(path)
        return con, path
    finally:
        _db.settings.db_path = orig_path

def test_db_composite_tables_absent():
    con, path = _run_init_db_on_temp()
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        for t in ("composite_results", "network_battery_runs", "network_entities", "network_registries"):
            assert t not in tables, f"Composite table '{t}' should have been dropped"
        con.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_db_practice_reputation_tables_present():
    con, path = _run_init_db_on_temp()
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        for t in ("practice_reputation_runs", "practice_reputation_practices"):
            assert t in tables, f"Table '{t}' missing from schema"
        con.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_db_analysis_runs_no_composite_mode_col():
    con, path = _run_init_db_on_temp()
    try:
        cols = {row[0] for row in con.execute("DESCRIBE analysis_runs").fetchall()}
        assert "composite_mode" not in cols, \
            "composite_mode column should have been dropped from analysis_runs"
        con.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)
