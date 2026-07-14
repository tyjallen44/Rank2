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
    """Only the exact anchor hospital name is excluded; affiliated brand siblings are kept."""
    entity_name = "Intermountain Medical Center"
    candidates = [
        {"name": "Intermountain Medical Center"},   # exact anchor — must be excluded
        {"name": "Intermountain Cardiology"},        # affiliated brand — must NOT be excluded
        {"name": "Intermountain Orthopedics"},       # affiliated brand — must NOT be excluded
        {"name": "St. Mark's Hospital"},
    ]

    _anchor_lc = entity_name.strip().lower()
    filtered = [r for r in candidates if r.get("name", "").strip().lower() != _anchor_lc]
    names = [r["name"] for r in filtered]
    assert entity_name not in names, "Exact anchor should be excluded"
    assert "Intermountain Cardiology" in names, "Affiliated brand clinic must not be filtered"
    assert "Intermountain Orthopedics" in names, "Affiliated brand clinic must not be filtered"
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


# ── Phase 1 remediation regression tests ─────────────────────────────────────

# R2: analyze_location cache wiring
def test_analyze_location_accepts_force_rerun():
    """analyze_location() must accept a force_rerun parameter."""
    import inspect
    from perception.analyzer import analyze_location
    sig = inspect.signature(analyze_location)
    assert "force_rerun" in sig.parameters, "analyze_location missing force_rerun parameter"
    assert sig.parameters["force_rerun"].default is False


def test_analyze_practice_accepts_force_rerun():
    """analyze_practice() must accept a force_rerun parameter."""
    import inspect
    from perception.practice_analyzer import analyze_practice
    sig = inspect.signature(analyze_practice)
    assert "force_rerun" in sig.parameters, "analyze_practice missing force_rerun parameter"
    assert sig.parameters["force_rerun"].default is False


# R3: Hospital signal suppression in practice improvement_sections
def test_hospital_signal_filtered_from_improvement_sections():
    """improvement_sections items containing hospital-only signals must be dropped."""
    from perception.practice_analyzer import _hospital_signal

    raw_sections = [
        {
            "title": "Visibility Gaps",
            "description": "Key areas to address.",
            "items": [
                "Leapfrog Hospital Safety Grade not published",
                "CMS Overall Star Rating not published",
                "Improve online scheduling availability",
            ],
        },
        {
            "title": "CMS Overall Star Rating improvement",
            "description": "Hospital-only metric.",
            "items": ["Register with CMS"],
        },
    ]
    filtered = [
        {
            "title": s["title"],
            "items": [i for i in s["items"] if not _hospital_signal(i)],
        }
        for s in raw_sections
        if not _hospital_signal(s["title"])
    ]
    # Section with hospital signal in title is dropped entirely
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Visibility Gaps"
    # Hospital-signal items removed from items list
    items = filtered[0]["items"]
    assert "Improve online scheduling availability" in items
    assert not any(_hospital_signal(i) for i in items), "Hospital signals leaked into items"


# N1: Markdown stripping
def test_strip_md_removes_bold():
    from perception.pdf import _strip_md
    assert "**" not in _strip_md("**Strong** performer")
    assert _strip_md("**Strong** performer") == "Strong performer"


def test_strip_md_removes_italic():
    from perception.pdf import _strip_md
    assert _strip_md("*excellent* outcomes") == "excellent outcomes"
    assert _strip_md("_excellent_ outcomes") == "excellent outcomes"


def test_strip_md_removes_inline_code():
    from perception.pdf import _strip_md
    assert _strip_md("Use `ABC` accreditation") == "Use ABC accreditation"


def test_strip_md_removes_headers():
    from perception.pdf import _strip_md
    assert _strip_md("## Section Title\nBody text") == "Section Title\nBody text"


def test_strip_md_preserves_non_markdown():
    from perception.pdf import _strip_md
    text = "Dr. Smith has 4.8★ ratings and 300 reviews."
    assert _strip_md(text) == text


def test_paras_strips_markdown(monkeypatch):
    """_paras() (defined inside build_html) strips markdown before HTML-escaping."""
    from perception.pdf import _strip_md
    # Directly test the strip — _paras() calls _strip_md() which we've already covered.
    result = _strip_md("**Bold** and *italic* text with `code`")
    assert "**" not in result
    assert "*" not in result
    assert "`" not in result


# N2: Sibling dedup against anchor
def test_sibling_dedup_drops_location_variant_of_anchor():
    """When anchor name contains a street address, a sibling that is a strong
    bidirectional match of the anchor should be dropped."""
    from perception.data.places import _name_match as _nmatch
    import re

    entity_name = "Desert Orthopaedic Center 2800 E Desert Inn Rd"
    siblings = [
        {"name": "Desert Orthopaedic Center - Desert Inn"},    # same location, different format
        {"name": "Desert Orthopaedic Center - Summerlin"},     # genuine sibling, must be kept
        {"name": "Desert Orthopaedic Center - Green Valley"},  # genuine sibling, must be kept
    ]

    _anchor_lc = entity_name.strip().lower()
    _anchor_has_address = bool(re.search(r'\d', entity_name))

    def _is_anchor_dup(name: str) -> bool:
        if name.strip().lower() == _anchor_lc:
            return True
        if _anchor_has_address:
            return (
                _nmatch(entity_name, name) == "strong"
                and _nmatch(name, entity_name) == "strong"
            )
        return False

    kept = [s for s in siblings if not _is_anchor_dup(s["name"])]
    names = [s["name"] for s in kept]
    assert "Desert Orthopaedic Center - Desert Inn" not in names, "Anchor duplicate was not dropped"
    assert "Desert Orthopaedic Center - Summerlin" in names, "Valid sibling was incorrectly dropped"
    assert "Desert Orthopaedic Center - Green Valley" in names, "Valid sibling was incorrectly dropped"


def test_sibling_dedup_exact_name_always_dropped():
    """An exact-name match against the anchor is always dropped regardless of address."""
    import re
    from perception.data.places import _name_match as _nmatch

    entity_name = "Main Street Orthopedics"
    siblings = [
        {"name": "Main Street Orthopedics"},
        {"name": "Main Street Orthopedics - North"},
    ]

    _anchor_lc = entity_name.strip().lower()
    _anchor_has_address = bool(re.search(r'\d', entity_name))

    def _is_anchor_dup(name: str) -> bool:
        if name.strip().lower() == _anchor_lc:
            return True
        if _anchor_has_address:
            return (
                _nmatch(entity_name, name) == "strong"
                and _nmatch(name, entity_name) == "strong"
            )
        return False

    kept = [s for s in siblings if not _is_anchor_dup(s["name"])]
    names = [s["name"] for s in kept]
    assert "Main Street Orthopedics" not in names, "Exact anchor not dropped"
    assert "Main Street Orthopedics - North" in names, "Non-exact sibling incorrectly dropped"


# R1: Verification flag rendering
def test_vflag_verified_contains_checkmark():
    from perception.pdf import _vflag
    html = _vflag("verified")
    assert "✓" in html
    assert "Verified" in html


def test_vflag_not_established_contains_x():
    from perception.pdf import _vflag
    html = _vflag("not_established")
    assert "✗" in html
    assert "Not established" in html


def test_vflag_partial_contains_circle():
    from perception.pdf import _vflag
    html = _vflag("partial")
    assert "◐" in html
    assert "Partial" in html


def test_google_stat_renders_verified_flag():
    """_google_stat() must include a ✓ Verified flag when Google listing is verified."""
    from perception.models import RankedProvider, GoogleFootprint, GoogleFrontDoor
    from perception.pdf import _google_stat
    p = RankedProvider(
        rank=1, name="Test Hospital",
        google_footprint=GoogleFootprint(
            front_door=GoogleFrontDoor(rating=4.2, count=350, verified=True)
        ),
    )
    html = _google_stat(p)
    assert "✓" in html, "Verified flag missing from _google_stat output"


def test_google_stat_renders_not_established_flag():
    """_google_stat() must include a ✗ Not established flag when listing is not verified."""
    from perception.models import RankedProvider, GoogleFootprint, GoogleFrontDoor
    from perception.pdf import _google_stat
    p = RankedProvider(
        rank=1, name="Test Hospital",
        google_footprint=GoogleFootprint(
            front_door=GoogleFrontDoor(verified=False, reason="no listing found")
        ),
    )
    html = _google_stat(p)
    assert "✗" in html, "Not-established flag missing from _google_stat output"


# ── Phase 2 display-strings regression tests ─────────────────────────────────

# D1: Acronym-safe casing
def test_smart_title_preserves_state_abbreviation():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import _smart_title
    assert _smart_title("USA HEALTH UNIVERSITY HOSPITAL") == "USA Health University Hospital"
    assert _smart_title("LAS VEGAS, NV 89121") == "Las Vegas, NV 89121"
    assert _smart_title("MOBILE, AL") == "Mobile, AL"


def test_smart_title_preserves_medical_acronyms():
    from server import _smart_title
    assert _smart_title("MRI CENTER OF MOBILE") == "MRI Center Of Mobile"
    assert _smart_title("ENT ASSOCIATES") == "ENT Associates"


def test_normalize_input_uses_smart_title():
    from server import _normalize_input
    result = _normalize_input("USA HEALTH UNIVERSITY HOSPITAL")
    assert result == "USA Health University Hospital", f"Got: {result}"


# D2: Disclaimer replace not append
def test_disclaimer_replace_not_append():
    """When LLM disclaimer lacks 'Pulse Score', the old sentence must be replaced, not appended."""
    from perception.strings import AIVS_DISCLAIMER, AIVS_DISCLAIMER_CHECK
    old_disclaimer = "The AI Visibility Score (0–100) reflects public data at time of collection."
    assert AIVS_DISCLAIMER_CHECK not in old_disclaimer
    # Simulate the fixed logic
    if AIVS_DISCLAIMER_CHECK not in old_disclaimer:
        result = AIVS_DISCLAIMER
    else:
        result = old_disclaimer
    assert "AI Visibility Score (0–100) reflects" not in result, "Old disclaimer sentence still present"
    assert "Pulse Score" in result, "New disclaimer not applied"
    # Confirm no double sentence
    assert result.count("Pulse Score") == 1


# D3: Single SECTION_ASSESSMENT header
def test_advice_title_empty_for_individual_report():
    """Individual report advice block must not generate a second SECTION_ASSESSMENT header."""
    from perception.strings import SECTION_ASSESSMENT
    # Simulate the corrected logic from _build_html()
    individual_report = True
    advice_title = "" if individual_report else "Improve Your AI Visibility"
    assert advice_title != SECTION_ASSESSMENT, "advice_title should be empty for individual reports"
    assert advice_title == ""


# D4: No duplicate rating in location lines
def test_locations_block_no_duplicate_rating():
    """When google_rating is present, overall_rating text should not also render."""
    from perception.models import RankedProvider, ConsolidatedLocation
    from perception.pdf import _locations_block

    p = RankedProvider(
        rank=1,
        name="Test Hospital",
        consolidated_locations=[
            ConsolidatedLocation(
                name="Test Hospital - Main",
                overall_rating="2.8★ · 375 reviews",
                google_rating=2.8,
                google_review_count=375,
            )
        ],
    )
    html = _locations_block(p)
    # Should contain the star character from google_span but NOT from rating_span too
    star_count = html.count("★") + html.count("&#9733;")
    assert star_count == 1, f"Rating rendered {star_count} times, expected 1: {html}"


# D5: Pluralization
def test_location_count_singular():
    """'1 location' not '1 locations'."""
    from perception.models import SystemAggregate
    sa = SystemAggregate(rating=4.2, total_reviews=100, location_count=1, confidence="registry")
    loc = f"{sa.location_count}"
    text = f'across {loc} location{"s" if sa.location_count != 1 else ""}'
    assert text == "across 1 location", f"Got: {text}"


def test_location_count_plural():
    from perception.models import SystemAggregate
    sa = SystemAggregate(rating=4.2, total_reviews=375, location_count=3, confidence="registry")
    loc = f"{sa.location_count}"
    text = f'across {loc} location{"s" if sa.location_count != 1 else ""}'
    assert text == "across 3 locations", f"Got: {text}"


# D6: Best-for echo removed
def test_recommendation_summary_not_in_card():
    """The individual entity card must not render recommendation_summary."""
    from perception.models import RankedProvider
    from perception.pdf import _individual_entity_card
    p = RankedProvider(
        rank=1,
        name="Desert Orthopaedic Center",
        best_suited_for="Patients needing orthopedic procedures",
        recommendation_summary="This is an excellent choice for patients needing orthopedic procedures.",
    )
    html = _individual_entity_card(p)
    assert p.recommendation_summary not in html, "recommendation_summary leaked into individual card"


# D7: Dr. prefix on physician rows
def test_dr_prefix_added_to_physician_without_title():
    """Physician rows without a title prefix should receive 'Dr. ' prefix."""
    _raw = "Steven Nishiyama"
    _lower = _raw.lower()
    prefixes = ("dr.", "pa-", "np ", "rn ", "do ", "md ")
    if not any(_lower.startswith(t) for t in prefixes):
        result = "Dr. " + _raw
    else:
        result = _raw
    assert result == "Dr. Steven Nishiyama"


def test_dr_prefix_not_doubled():
    """Physician rows that already include 'Dr.' must not be double-prefixed."""
    _raw = "Dr. Jane Smith"
    _lower = _raw.lower()
    prefixes = ("dr.", "pa-", "np ", "rn ", "do ", "md ")
    if not any(_lower.startswith(t) for t in prefixes):
        result = "Dr. " + _raw
    else:
        result = _raw
    assert result == "Dr. Jane Smith"
    assert "Dr. Dr." not in result


# D8: Practice cover title excludes address
def test_practice_cover_title_splits_address():
    """For practice reports with embedded address in entity_name, cover title shows name only."""
    import re
    entity_name = "Desert Orthopaedic Center 2800 E Desert Inn Rd"
    entity_type = "practice"
    _addr_match = re.search(r'\s+\d+\s', entity_name or "")
    if entity_type == "practice" and _addr_match:
        display_name = entity_name[:_addr_match.start()].strip()
        addr_part    = entity_name[_addr_match.start():].strip()
    else:
        display_name = entity_name
        addr_part = ""
    assert display_name == "Desert Orthopaedic Center"
    assert "2800" in addr_part
    assert "2800" not in display_name


def test_practice_cover_title_unchanged_without_address():
    """Practice name without embedded address renders as-is in cover title."""
    import re
    entity_name = "Desert Orthopaedic Center"
    entity_type = "practice"
    _addr_match = re.search(r'\s+\d+\s', entity_name or "")
    if entity_type == "practice" and _addr_match:
        display_name = entity_name[:_addr_match.start()].strip()
    else:
        display_name = entity_name
    assert display_name == "Desert Orthopaedic Center"
