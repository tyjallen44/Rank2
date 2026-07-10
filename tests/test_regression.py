"""Tier 1/2 regression: verify Composite additions didn't alter existing outputs.

These are structural/import-only tests. Byte-identical PDF output requires a
running server + Claude API, so we verify that:
  1. All original pdf.py functions still exist and are callable.
  2. Adding composite_mode to AnalysisResult doesn't break existing serialization.
  3. db.py schema additions don't clobber existing tables.
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
    for fn in ("render_pdf", "render_comparison_pdf", "render_composite_pdf"):
        assert callable(getattr(pdf, fn, None)), f"pdf.{fn} missing"

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


# ── models.py unchanged for existing fields ───────────────────────────────────

def test_analysis_result_existing_fields():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(
        run_id="test-001",
        location="Mobile, Alabama",
        generated_at=date.today(),
    )
    # composite_mode defaults to None — old code unaffected
    assert r.composite_mode is None
    assert r.aggregate is False
    assert r.teaser_report is False

def test_analysis_result_composite_mode_field():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(
        run_id="test-002",
        location="Mobile, Alabama",
        generated_at=date.today(),
        composite_mode="hospitals_only",
    )
    assert r.composite_mode == "hospitals_only"

def test_analysis_result_serializes_without_composite():
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(run_id="x", location="y", generated_at=date.today())
    d = r.model_dump()
    assert "composite_mode" in d
    assert d["composite_mode"] is None


# ── composite_config.py constants ────────────────────────────────────────────

def test_composite_config_keys_present():
    from perception.composite_config import COMPOSITE_CONFIG
    required = [
        "attribution_gate", "footprint_classes", "continuum_bonus",
        "ceilings", "small_network", "inclusion_weights",
        "sar_tier2_weight", "sar_battery_weight",
    ]
    for key in required:
        assert key in COMPOSITE_CONFIG, f"COMPOSITE_CONFIG missing '{key}'"

def test_composite_config_no_inline_magic():
    """Verify ceilings dict has all referenced keys so scoring never KeyErrors."""
    from perception.composite_config import COMPOSITE_CONFIG
    c = COMPOSITE_CONFIG["ceilings"]
    for key in ("attribution_sar_threshold", "attribution_max_lift",
                "orphan_volume_threshold", "orphan_cap", "no_masking_max_lift"):
        assert key in c


# ── db.py tables exist after init ────────────────────────────────────────────

def _run_init_db_on_temp():
    """Run init_db() against a fresh temp DB and return (con, tmp_path)."""
    import duckdb
    import tempfile
    from perception import db as _db
    from perception.db import init_db

    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)  # DuckDB must create it from scratch

    orig_path = _db.settings.db_path
    try:
        _db.settings.db_path = path
        init_db()
        con = duckdb.connect(path)
        return con, path
    finally:
        _db.settings.db_path = orig_path

def test_db_new_tables_exist():
    con, path = _run_init_db_on_temp()
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        for t in ("network_registries", "network_entities", "network_battery_runs", "composite_results"):
            assert t in tables, f"Table '{t}' missing from schema"
        con.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_db_analysis_runs_has_composite_mode():
    con, path = _run_init_db_on_temp()
    try:
        cols = {row[0] for row in con.execute("DESCRIBE analysis_runs").fetchall()}
        assert "composite_mode" in cols
        con.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)
