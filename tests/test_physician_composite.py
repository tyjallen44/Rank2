"""Tests for the physician-level Practice Composite feature.

Run with: python -m pytest tests/test_physician_composite.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_stream_mock_physician(physicians_payload: list[dict]):
    """Build a mock client whose messages.stream() returns given physician data."""
    final_msg = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_physician_reputation"
    tool_block.input = {"physicians": physicians_payload}
    final_msg.content = [tool_block]

    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
    stream_ctx.__exit__ = MagicMock(return_value=False)
    stream_ctx.get_final_message = MagicMock(return_value=final_msg)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = stream_ctx
    return mock_client


def _make_physician(**kwargs):
    defaults = {
        "name": "Jane Smith", "credential": "MD",
        "npi": "1234567890", "specialty": "Orthopedic Surgery",
    }
    return {**defaults, **kwargs}


def _make_ph_result(**kwargs):
    defaults = {
        "physician_name": "Dr. Jane Smith",
        "npi": "1234567890",
        "specialty": "Orthopedic Surgery",
        "credential": "MD",
        "parent_entity": "Test Clinic",
        "row_type": "physician",
        "not_established": False,
        "avg_rating": 4.3,
        "total_reviews": 87,
        "platforms_found": 2,
        "platforms_list": "Healthgrades, Vitals",
        "platform_entries": [("healthgrades", 60, None), ("vitals", 27, None)],
        "collection_date": "2026-07-13",
        "google_rating": None, "google_count": None, "google_url": None,
        "healthgrades_rating": 4.5, "healthgrades_count": 60, "healthgrades_url": None,
        "vitals_rating": 4.0, "vitals_count": 27, "vitals_url": None,
        "webmd_rating": None, "webmd_count": None, "webmd_url": None,
        "yelp_rating": None, "yelp_count": None, "yelp_url": None,
        "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
        "primary_url": None,
    }
    return {**defaults, **kwargs}


# ── (a) Checkbox dependency ───────────────────────────────────────────────────

def test_physician_composite_checkbox_present_in_ir():
    """ir-physician-composite checkbox must exist in the Individual Report page HTML."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
    html = open(html_path).read()
    assert 'id="ir-physician-composite"' in html


def test_physician_composite_checkbox_disabled_by_default():
    """ir-physician-composite must start disabled (parent unchecked)."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
    html = open(html_path).read()
    # Find the checkbox and confirm 'disabled' appears on or near it
    idx = html.index('id="ir-physician-composite"')
    # The disabled attribute should be within the same <input> tag
    tag_end = html.index('>', idx)
    tag = html[max(0, idx - 100):tag_end + 1]
    assert 'disabled' in tag


def test_physician_composite_checkbox_absent_from_comparison():
    """ir-physician-composite must NOT appear in the Comparison Report section."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
    html = open(html_path).read()
    # It should appear exactly once (IR only)
    assert html.count('id="ir-physician-composite"') == 1
    # Confirm it's not in any cmp- block by checking no cmp-physician element exists
    assert 'id="cmp-a-physician' not in html
    assert 'id="cmp-b-physician' not in html


def test_onpracticecompositechange_function_present():
    """The onPracticeCompositeChange wiring function must exist in JS."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")
    html = open(html_path).read()
    assert 'function onPracticeCompositeChange' in html
    assert 'onPracticeCompositeChange()' in html


# ── (b) Identity corroboration rule ──────────────────────────────────────────

def test_identity_collision_returns_null_for_ambiguous_profile(monkeypatch):
    """Common-name collision: when Claude returns null for a physician,
    collect_physician_data records not_established=True, not a fabricated profile."""
    from perception import physician_reputation as pr

    # Two physicians named "John Smith" — Claude can only corroborate one
    physicians = [
        {"name": "John Smith", "credential": "MD", "npi": "1111111111", "specialty": "Cardiology"},
        {"name": "John Smith", "credential": "DO", "npi": None,         "specialty": "Family Medicine"},
    ]
    # Claude returns null for the DO (no NPI, can't corroborate)
    payload = [
        {"name": "John Smith",
         "healthgrades_rating": 4.5, "healthgrades_count": 80,
         "healthgrades_url": "https://www.healthgrades.com/physician/john-smith-md",
         "vitals_rating": None, "vitals_count": None, "vitals_url": None,
         "webmd_rating": None, "webmd_count": None, "webmd_url": None,
         "yelp_rating": None, "yelp_count": None, "yelp_url": None,
         "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
         "google_rating": None, "google_count": None, "google_url": None},
        # Claude cannot corroborate the DO — returns all nulls
        {"name": "John Smith",
         "healthgrades_rating": None, "healthgrades_count": None, "healthgrades_url": None,
         "vitals_rating": None, "vitals_count": None, "vitals_url": None,
         "webmd_rating": None, "webmd_count": None, "webmd_url": None,
         "yelp_rating": None, "yelp_count": None, "yelp_url": None,
         "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
         "google_rating": None, "google_count": None, "google_url": None},
    ]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock_physician(payload))

    results = pr.collect_physician_data(
        physicians, "Test Cardiology", "Memphis", "TN"
    )
    # Both named "John Smith" so platform_data dict holds one entry (last one wins for DO since it's duped)
    # The important assertion: no result should have fabricated data if it got null from Claude
    assert len(results) == 2
    # At least one must be not_established (the ambiguous DO)
    not_est = [r for r in results if r["not_established"]]
    assert len(not_est) >= 1


def test_corroborated_physician_gets_profile(monkeypatch):
    """A physician with a clean name and NPI gets their profile attached."""
    from perception import physician_reputation as pr

    physicians = [{"name": "Alice Chen", "credential": "MD",
                   "npi": "9876543210", "specialty": "Oncology"}]
    payload = [{
        "name": "Alice Chen",
        "healthgrades_rating": 4.8, "healthgrades_count": 120,
        "healthgrades_url": "https://www.healthgrades.com/physician/alice-chen",
        "vitals_rating": None, "vitals_count": None, "vitals_url": None,
        "webmd_rating": None, "webmd_count": None, "webmd_url": None,
        "yelp_rating": None, "yelp_count": None, "yelp_url": None,
        "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
        "google_rating": None, "google_count": None, "google_url": None,
    }]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock_physician(payload))

    results = pr.collect_physician_data(physicians, "Test Oncology", "Memphis", "TN")
    assert len(results) == 1
    row = results[0]
    assert not row["not_established"]
    assert row["avg_rating"] == pytest.approx(4.8)
    assert row["total_reviews"] == 120
    assert row["physician_name"] == "Dr. Alice Chen"
    assert row["healthgrades_url"] == "https://www.healthgrades.com/physician/alice-chen"


# ── (c) Nested rendering ──────────────────────────────────────────────────────

def test_physician_sub_rows_rendered_under_parent_practice():
    """Physician rows appear after their parent practice row in the HTML."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "OrthoSouth Clinic", "not_established": False,
        "avg_rating": 4.2, "total_reviews": 200, "platforms_found": 2,
        "platforms_list": "Google, Healthgrades", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
        "physicians": [
            _make_ph_result(physician_name="Dr. Jane Smith", total_reviews=87),
            _make_ph_result(physician_name="Dr. Bob Jones", total_reviews=40),
        ],
    }
    html = _practice_reputation_table_html([practice_row])
    practice_pos = html.index("OrthoSouth Clinic")
    smith_pos    = html.index("Dr. Jane Smith")
    jones_pos    = html.index("Dr. Bob Jones")
    assert practice_pos < smith_pos
    assert practice_pos < jones_pos


def test_physician_sub_rows_sorted_by_reviews_desc():
    """Physician sub-rows sort by total_reviews descending within their practice group."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 4.0, "total_reviews": 100, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
        "physicians": [
            _make_ph_result(physician_name="Dr. Low Reviews",  total_reviews=5),
            _make_ph_result(physician_name="Dr. High Reviews", total_reviews=200),
        ],
    }
    html = _practice_reputation_table_html([practice_row])
    high_pos = html.index("Dr. High Reviews")
    low_pos  = html.index("Dr. Low Reviews")
    assert high_pos < low_pos


def test_practice_row_totals_unchanged_by_physicians():
    """Practice row Avg Rating and Total Reviews are not affected by physician rows."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 3.9, "total_reviews": 55, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
        "physicians": [
            _make_ph_result(physician_name="Dr. X", avg_rating=4.9, total_reviews=999),
        ],
    }
    html = _practice_reputation_table_html([practice_row])
    # Practice total (55) appears before the physician total (999)
    assert "55" in html
    practice_total_pos = html.index(">55<")
    physician_total_pos = html.index(">999<")
    assert practice_total_pos < physician_total_pos


def test_physician_sub_rows_visually_distinct():
    """Physician rows have indented padding to visually separate from practice rows."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 4.0, "total_reviews": 100, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
        "physicians": [_make_ph_result()],
    }
    html = _practice_reputation_table_html([practice_row])
    # Physician name cell must have greater left padding than practice name cell
    # Practice cell uses padding:9px 12px; physician cell uses padding-left:28px
    assert "28px" in html


# ── (d) Not-established physician row ────────────────────────────────────────

def test_not_established_physician_renders_dash(monkeypatch):
    """A physician found on zero platforms renders the Not established cell."""
    from perception import physician_reputation as pr

    physicians = [{"name": "Unknown Doctor", "credential": "MD", "npi": None, "specialty": None}]
    payload = [{
        "name": "Unknown Doctor",
        "healthgrades_rating": None, "healthgrades_count": None, "healthgrades_url": None,
        "vitals_rating": None, "vitals_count": None, "vitals_url": None,
        "webmd_rating": None, "webmd_count": None, "webmd_url": None,
        "yelp_rating": None, "yelp_count": None, "yelp_url": None,
        "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
        "google_rating": None, "google_count": None, "google_url": None,
    }]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock_physician(payload))

    results = pr.collect_physician_data(physicians, "Test Clinic", "Memphis", "TN")
    assert len(results) == 1
    assert results[0]["not_established"] is True
    assert results[0]["avg_rating"] is None
    assert results[0]["total_reviews"] == 0


def test_not_established_physician_renders_in_html():
    """Not-established physician row shows the italic 'Not established' text."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 4.0, "total_reviews": 100, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
        "physicians": [
            _make_ph_result(physician_name="Dr. Ghost", not_established=True,
                            avg_rating=None, total_reviews=0, platforms_found=0,
                            platforms_list="", platform_entries=[]),
        ],
    }
    html = _practice_reputation_table_html([practice_row])
    assert "Not established" in html


# ── (e) Hospital-typed rows produce no physician sub-rows ────────────────────

def test_org_level_physician_discovery_uses_entity_name():
    """discover_physicians is called once with the org entity name, not per-clinic sub-location."""
    import perception.physician_discovery as pd_mod
    import unittest.mock as um

    called_with = []

    def fake_discover(practice_name, city, state, on_event=None):
        called_with.append(practice_name)
        return [{"name": "Dr. Test", "credential": "MD", "npi": None, "specialty": None}]

    practice_rows = [
        {"practice_name": "OrthoSouth - Main", "entity_type": "practice", "is_anchor": True,
         "city": "Memphis", "state": "TN"},
        {"practice_name": "OrthoSouth - Southwind", "entity_type": "practice",
         "city": "Memphis", "state": "TN"},
        {"practice_name": "OrthoSouth - Bartlett", "entity_type": "practice",
         "city": "Bartlett", "state": "TN"},
    ]
    entity_name = "OrthoSouth"
    city, state = "Memphis", "TN"

    # New org-level logic: one call with entity_name, attached to anchor row
    with um.patch("perception.physician_discovery.discover_physicians", side_effect=fake_discover):
        target_row = (
            next((r for r in practice_rows if r.get("is_anchor")), None)
            or next((r for r in practice_rows if r.get("entity_type") != "hospital"), None)
        )
        if target_row:
            physicians = fake_discover(entity_name, city, state)
            if physicians:
                target_row["physicians"] = physicians

    # Must be called exactly once with the org name, never with sub-location names
    assert called_with == [entity_name]
    assert "OrthoSouth - Southwind" not in called_with
    assert "OrthoSouth - Bartlett" not in called_with
    # Physicians are attached only to the anchor row
    anchor = next(r for r in practice_rows if r.get("is_anchor"))
    assert "physicians" in anchor
    other_rows = [r for r in practice_rows if not r.get("is_anchor")]
    for r in other_rows:
        assert "physicians" not in r


def test_hospital_row_renders_no_physician_sub_rows():
    """A practice row with entity_type='hospital' and physicians=[] renders no sub-rows."""
    from perception.pdf import _practice_reputation_table_html

    practice_row = {
        "practice_name": "Regional Medical Center", "entity_type": "hospital",
        "not_established": False, "avg_rating": 4.1, "total_reviews": 300,
        "platforms_found": 2, "platforms_list": "Google, Healthgrades",
        "affiliation_verified": True, "collection_date": "2026-07-13",
        "primary_url": None, "platform_entries": None,
        # No physicians key → no sub-rows
    }
    html = _practice_reputation_table_html([practice_row])
    # Only one data row (the practice itself) — no physician sub-rows
    assert "28px" not in html


# ── (f) CSV row_type / parent_entity columns ─────────────────────────────────

def test_physician_result_has_row_type_and_parent_entity(monkeypatch):
    """Every physician result dict must carry row_type='physician' and parent_entity."""
    from perception import physician_reputation as pr

    physicians = [{"name": "Sam Lee", "credential": "NP", "npi": None, "specialty": "Cardiology"}]
    payload = [{
        "name": "Sam Lee",
        "healthgrades_rating": 4.0, "healthgrades_count": 25, "healthgrades_url": None,
        "vitals_rating": None, "vitals_count": None, "vitals_url": None,
        "webmd_rating": None, "webmd_count": None, "webmd_url": None,
        "yelp_rating": None, "yelp_count": None, "yelp_url": None,
        "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
        "google_rating": None, "google_count": None, "google_url": None,
    }]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock_physician(payload))

    results = pr.collect_physician_data(physicians, "Heart Clinic", "Nashville", "TN")
    row = results[0]
    assert row["row_type"] == "physician"
    assert row["parent_entity"] == "Heart Clinic"


def test_physician_result_has_all_url_columns(monkeypatch):
    """Physician result dicts include per-platform URL columns."""
    from perception import physician_reputation as pr

    physicians = [{"name": "Maria Torres", "credential": "MD", "npi": "5551234567",
                   "specialty": "Dermatology"}]
    url = "https://www.healthgrades.com/physician/maria-torres"
    payload = [{
        "name": "Maria Torres",
        "healthgrades_rating": 4.6, "healthgrades_count": 90, "healthgrades_url": url,
        "vitals_rating": None, "vitals_count": None, "vitals_url": None,
        "webmd_rating": None, "webmd_count": None, "webmd_url": None,
        "yelp_rating": None, "yelp_count": None, "yelp_url": None,
        "ratemds_rating": None, "ratemds_count": None, "ratemds_url": None,
        "google_rating": None, "google_count": None, "google_url": None,
    }]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock_physician(payload))

    results = pr.collect_physician_data(physicians, "Derm Clinic", "Atlanta", "GA")
    row = results[0]
    for col in ("google_url", "healthgrades_url", "vitals_url", "webmd_url",
                "yelp_url", "ratemds_url", "primary_url"):
        assert col in row, f"URL column '{col}' missing"
    assert row["healthgrades_url"] == url
    assert row["primary_url"] == url


# ── (g) Regression: Practice Composite without physicians is unchanged ────────

def test_practice_composite_rows_without_physicians_unchanged():
    """When physicians list is absent/empty, practice rows render identically to before."""
    from perception.pdf import _practice_reputation_table_html

    row_without = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 4.0, "total_reviews": 100, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-13", "primary_url": None, "platform_entries": None,
    }
    row_with_empty = dict(row_without, physicians=[])

    html_without = _practice_reputation_table_html([row_without])
    html_with_empty = _practice_reputation_table_html([row_with_empty])
    assert html_without == html_with_empty


def test_physician_composite_rows_empty_by_default():
    """AnalysisResult.physician_composite_rows defaults to empty list."""
    from perception.models import AnalysisResult
    from datetime import date
    r = AnalysisResult(run_id="x", location="Memphis, TN", generated_at=date.today())
    assert r.physician_composite_rows == []


def test_db_physician_table_present():
    """practice_reputation_physicians table must exist after init_db()."""
    from perception.db import init_db, get_connection
    init_db()
    con = get_connection()
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()}
    con.close()
    assert "practice_reputation_physicians" in tables
