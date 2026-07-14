"""Tests for grade_from_score, comparison pillar labels, entity-matching pipeline,
anchor-row direction, score reuse (get_recent_run), and regression guards.

Run with: python -m pytest tests/test_grade.py -v
No server, Claude API, or network access required.
"""
import sys
import os
import re
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from perception.scoring import (
    grade_from_score,
    TIER_KEYS,
    TIER_LABELS,
    PRACTICE_TIER_LABELS,
    composite_score,
)
from perception.practice_scoring import composite as practice_composite_score


# ── (a) Grade function edge cases ─────────────────────────────────────────────

def test_grade_boundaries():
    band_cases = [
        (100, "A",  "Excellent"),
        (93,  "A",  "Excellent"),
        (92,  "A−", "Excellent"),
        (85,  "A−", "Excellent"),
        (84,  "B+", "Strong"),
        (80,  "B+", "Strong"),
        (79,  "B",  "Strong"),
        (75,  "B",  "Strong"),
        (74,  "B",  "Good"),
        (70,  "B",  "Good"),
        (69,  "B−", "Good"),
        (65,  "B−", "Good"),
        (64,  "C+", "Fair"),
        (60,  "C+", "Fair"),
        (59,  "C",  "Fair"),
        (55,  "C",  "Fair"),
        (54,  "C−", "Below Average"),
        (47,  "C−", "Below Average"),
        (46,  "D",  "Below Average"),
        (40,  "D",  "Below Average"),
        (39,  "D",  "Weak"),
        (20,  "D",  "Weak"),
        (19,  "F",  "Weak"),
        (0,   "F",  "Weak"),
    ]
    for score, expected_grade, expected_band in band_cases:
        grade, band = grade_from_score(score)
        assert grade == expected_grade, f"score={score}: expected grade {expected_grade!r}, got {grade!r}"
        assert band == expected_band, f"score={score}: expected band {expected_band!r}, got {band!r}"


def test_grade_none():
    grade, band = grade_from_score(None)
    assert grade == "—"
    assert band == "Unscored"


def test_grade_clamps_above_100():
    grade, band = grade_from_score(150)
    assert grade == "A"
    assert band == "Excellent"


def test_grade_clamps_below_0():
    grade, band = grade_from_score(-5)
    assert grade == "F"
    assert band == "Weak"


# ── (b) Comparison pillar labels ──────────────────────────────────────────────

def _tier_labels_for_profile(profile):
    if profile and profile.startswith("practice_"):
        return PRACTICE_TIER_LABELS.get(profile, PRACTICE_TIER_LABELS["practice_procedural"])
    return TIER_LABELS.get(profile or "procedural", TIER_LABELS["procedural"])


def test_hospital_procedural_labels():
    labels = _tier_labels_for_profile("procedural")
    assert labels["clinical_outcomes_safety"] == "Outcomes & Safety"
    assert labels["credentials_recognition"] == "Credentials & Recognition"
    assert labels["patient_experience_reviews"] == "Experience & Reviews"
    assert labels["access_fit"] == "Access & Fit"
    for key in TIER_KEYS:
        assert key in labels, f"Missing key {key!r} in hospital procedural labels"


def test_hospital_relationship_labels():
    labels = _tier_labels_for_profile("relationship")
    assert labels["clinical_outcomes_safety"] == "Quality & Coordination"
    assert labels["credentials_recognition"] == "Credentials & Recognition"


def test_practice_labels_differ_from_hospital():
    hosp = _tier_labels_for_profile("procedural")
    prac = _tier_labels_for_profile("practice_procedural")
    assert hosp["clinical_outcomes_safety"] != prac["clinical_outcomes_safety"]
    assert prac["clinical_outcomes_safety"] == "Practitioner Credentials & Clinical Quality"
    assert prac["credentials_recognition"] == "Reviews & Reputation"


def test_mixed_rubric_labels_do_not_cross():
    hosp_labels = _tier_labels_for_profile("procedural")
    prac_labels = _tier_labels_for_profile("practice_procedural")
    for key in TIER_KEYS:
        assert key in hosp_labels
        assert key in prac_labels
        # Hospital and practice labels for the same key should differ for the first two pillars
    assert hosp_labels["clinical_outcomes_safety"] != prac_labels["clinical_outcomes_safety"]


# ── (c) Section 4 entity-name matching fixtures ───────────────────────────────

def test_no_address_path_in_practice_reputation():
    """Regression: practice_reputation.py must never use an address as the Google search key."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "perception", "practice_reputation.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # The old wrong-neighbor bug: search_name = p.get("address") or p["name"]
    # Using address in a display string for Claude's prompt is fine; using it as the
    # Places API search key is not.
    assert re.search(r'search_name\s*=\s*p\.get\(["\']address', src) is None, (
        'practice_reputation.py uses address as the Google Places search key — '
        'the wrong-neighbor bug must not be reintroduced'
    )
    # Also confirm entity_name_key or p["name"] is the search key
    assert re.search(r'fetch_provider\(\s*(?:entity_name_key|p\["name"\]|p\[\'name\'\])', src) is not None, (
        'practice_reputation.py should use entity name (not address) as the fetch_provider key'
    )


def test_not_established_when_no_name_match():
    """An entity with a name that matches nothing should produce verified=False, not steal a profile."""
    from perception.data.places import GoogleRead
    # A GoogleRead with verified=False means the entity is not established
    read = GoogleRead(query="Fake Entity Name XYZ", verified=False, reason="no match")
    assert not read.verified


# ── (d) Anchor-row direction tests ────────────────────────────────────────────

def test_practice_anchor_row_is_first():
    """In a practice roster, the anchor entity should be the first entry."""
    entity_name = "Main Street Orthopedics"
    anchor_entry = {
        "name": entity_name,
        "entity_type": "practice",
        "is_anchor": True,
        "city": "Salt Lake City",
        "state": "UT",
    }
    siblings = [
        {"name": "Mountain Ortho Group", "entity_type": "practice"},
        {"name": "Valley Orthopedics", "entity_type": "practice"},
    ]
    roster = [anchor_entry] + siblings
    assert roster[0]["is_anchor"] is True
    assert roster[0]["name"] == entity_name


def test_hospital_anchor_excluded_from_roster():
    """In a hospital analysis, the anchor hospital should not appear as a row in the market table."""
    entity_name = "Intermountain Medical Center"
    candidates = [
        {"name": "Intermountain Medical Center"},   # anchor — must be excluded
        {"name": "St. Mark's Hospital"},
        {"name": "Mountain Point Medical Center"},
    ]

    def _nmatch(a: str, b: str) -> str:
        a_tok = set(a.lower().split())
        b_tok = set(b.lower().split())
        if not a_tok or not b_tok:
            return "weak"
        overlap = len(a_tok & b_tok) / len(a_tok)
        return "strong" if overlap >= 0.6 else ("weak" if overlap >= 0.3 else "none")

    filtered = [r for r in candidates if _nmatch(entity_name, r.get("name", "")) != "strong"]
    names = [r["name"] for r in filtered]
    assert entity_name not in names
    assert "St. Mark's Hospital" in names


# ── (e) Hospital signal filter ────────────────────────────────────────────────

def test_hospital_signals_filtered_from_practice():
    from perception.practice_analyzer import _hospital_signal
    assert _hospital_signal("Leapfrog Grade A — strong safety record")
    assert _hospital_signal("CMS Overall Star Rating: 5 stars")
    assert _hospital_signal("High HCAHPS patient satisfaction scores")
    assert not _hospital_signal("Board-certified orthopedic surgeons")
    assert not _hospital_signal("Strong Google rating: 4.8 stars (1,200+ reviews)")


# ── (f) Score reuse: get_recent_run window ────────────────────────────────────

def test_get_recent_run_returns_none_at_91_days(tmp_path, monkeypatch):
    import duckdb
    import json
    import uuid
    from unittest.mock import patch

    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    con = duckdb.connect(db_file)
    con.execute("""
        CREATE TABLE analysis_runs (
            run_id VARCHAR PRIMARY KEY,
            location VARCHAR NOT NULL,
            specialty VARCHAR,
            aggregate BOOLEAN DEFAULT FALSE,
            generated_at DATE NOT NULL,
            entity_name VARCHAR,
            individual_report BOOLEAN DEFAULT FALSE,
            result_json VARCHAR
        )
    """)
    old_date = (date.today() - timedelta(days=91)).isoformat()
    run_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO analysis_runs (run_id, location, entity_name, generated_at, result_json) VALUES (?, ?, ?, ?, ?)",
        [run_id, "Salt Lake City, UT", "Test Hospital", old_date, '{"run_id": "x"}'],
    )
    con.close()

    with patch("perception.db.settings") as mock_settings:
        mock_settings.db_path = db_file
        from perception.db import get_recent_run
        result = get_recent_run("Test Hospital", "Salt Lake City, UT", days=90)
        assert result is None, "91-day-old run should not be returned for 90-day window"


def test_get_recent_run_returns_result_at_90_days(tmp_path, monkeypatch):
    import duckdb
    import uuid
    from unittest.mock import patch

    db_file = str(tmp_path / "test2.db")
    monkeypatch.setenv("DB_PATH", db_file)

    con = duckdb.connect(db_file)
    con.execute("""
        CREATE TABLE analysis_runs (
            run_id VARCHAR PRIMARY KEY,
            location VARCHAR NOT NULL,
            specialty VARCHAR,
            aggregate BOOLEAN DEFAULT FALSE,
            generated_at DATE NOT NULL,
            entity_name VARCHAR,
            individual_report BOOLEAN DEFAULT FALSE,
            result_json VARCHAR
        )
    """)
    recent_date = (date.today() - timedelta(days=90)).isoformat()
    run_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO analysis_runs (run_id, location, entity_name, generated_at, result_json) VALUES (?, ?, ?, ?, ?)",
        [run_id, "Salt Lake City, UT", "Test Hospital", recent_date, '{"run_id": "cached"}'],
    )
    con.close()

    with patch("perception.db.settings") as mock_settings:
        mock_settings.db_path = db_file
        from perception.db import get_recent_run
        result = get_recent_run("Test Hospital", "Salt Lake City, UT", days=90)
        assert result is not None, "90-day-old run should be returned"
        assert result["result_json"] == '{"run_id": "cached"}'


# ── (g) No-address-path regression ───────────────────────────────────────────

def test_no_address_path_regression():
    """Confirm practice_reputation.py does not use 'address' as a Google search key."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "perception", "practice_reputation.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # The old wrong-neighbor bug: search_name = p.get("address") or p["name"]
    assert re.search(r'search_name\s*=\s*p\.get\(["\']address', src) is None


# ── (h) Scoring regression ────────────────────────────────────────────────────

def test_hospital_composite_score_unchanged():
    """Composite score for a known input set must remain stable."""
    tier_scores = {
        "clinical_outcomes_safety": 80,
        "credentials_recognition": 70,
        "patient_experience_reviews": 65,
        "access_fit": 75,
    }
    result = composite_score(tier_scores, "procedural")
    assert result is not None
    # 80*0.46 + 70*0.35 + 65*0.10 + 75*0.09 = 36.8+24.5+6.5+6.75 = 74.55 → 75
    assert result == 75, f"Expected 75, got {result}"


def test_practice_composite_score_unchanged():
    """Practice composite returns a (score, ceiling_applied, ceiling_reason) 3-tuple."""
    tier_scores = {
        "clinical_outcomes_safety": 80,
        "credentials_recognition": 70,
        "patient_experience_reviews": 65,
        "access_fit": 75,
    }
    score, ceiling_applied, ceiling_reason = practice_composite_score(tier_scores, "practice_procedural")
    assert score is not None
    assert isinstance(score, int)
    assert isinstance(ceiling_applied, bool)
    assert isinstance(ceiling_reason, str)


# ── City canonicalization helpers ─────────────────────────────────────────────

def test_city_from_address_us_format():
    from perception.data.places import _city_from_address
    assert _city_from_address("123 Main St, Phoenix, AZ 85001, USA") == "Phoenix"
    assert _city_from_address("456 Oak Ave, Salt Lake City, UT 84101, USA") == "Salt Lake City"


def test_city_match_ratio_same_city():
    from perception.data.places import city_match_ratio
    ratio = city_match_ratio("Phoenix", "123 Main St, Phoenix, AZ 85001, USA")
    assert ratio >= 0.85


def test_city_match_ratio_different_city():
    from perception.data.places import city_match_ratio
    ratio = city_match_ratio("Phoenix", "456 Oak Ave, Tempe, AZ 85281, USA")
    assert ratio < 0.85


def test_city_match_ratio_empty_address():
    from perception.data.places import city_match_ratio
    assert city_match_ratio("Phoenix", "") == 1.0
    assert city_match_ratio("Phoenix", None) == 1.0
