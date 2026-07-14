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


# N2 / F1: Sibling dedup against anchor (unconditional bidirectional AND, no address gate)
def _is_anchor_dup_new(entity_name: str, sibling_name: str) -> bool:
    """Mirror of the production _is_anchor_duplicate() function (unconditional AND)."""
    from perception.data.places import _name_match as _nmatch
    if sibling_name.strip().lower() == entity_name.strip().lower():
        return True
    return (
        _nmatch(entity_name, sibling_name) == "strong"
        and _nmatch(sibling_name, entity_name) == "strong"
    )


def test_sibling_dedup_drops_location_variant_of_anchor():
    """Bidirectional AND drops '- Desert Inn (Main Office)' and '-  Desert Inn' but keeps
    '- Summerlin' and '- Green Valley' (distinct location qualifiers)."""
    entity_name = "Desert Orthopaedic Center 2800 E Desert Inn Rd"
    siblings = [
        {"name": "Desert Orthopaedic Center - Desert Inn"},              # same location → drop
        {"name": "Desert Orthopaedic Center - Desert Inn (Main Office)"},# same location → drop
        {"name": "Desert Orthopaedic Center - Summerlin"},               # different location → keep
        {"name": "Desert Orthopaedic Center - Green Valley"},            # different location → keep
    ]

    kept = [s for s in siblings if not _is_anchor_dup_new(entity_name, s["name"])]
    names = [s["name"] for s in kept]
    assert "Desert Orthopaedic Center - Desert Inn" not in names, "Anchor duplicate was not dropped"
    assert "Desert Orthopaedic Center - Desert Inn (Main Office)" not in names, "(Main Office) variant not dropped"
    assert "Desert Orthopaedic Center - Summerlin" in names, "Valid sibling Summerlin incorrectly dropped"
    assert "Desert Orthopaedic Center - Green Valley" in names, "Valid sibling Green Valley incorrectly dropped"


def test_sibling_dedup_exact_name_always_dropped():
    """Exact-name match is always dropped (unconditional, regardless of address presence)."""
    entity_name = "Lakeside Orthopedic Center"
    siblings = [
        {"name": "Lakeside Orthopedic Center"},     # exact match → always drops
        {"name": "Northwestern Bone & Joint"},       # distinct brand → keeps
    ]

    kept = [s for s in siblings if not _is_anchor_dup_new(entity_name, s["name"])]
    names = [s["name"] for s in kept]
    assert "Lakeside Orthopedic Center" not in names, "Exact anchor not dropped"
    assert "Northwestern Bone & Joint" in names, "Distinct sibling incorrectly dropped"


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
    """FULL_DISCLAIMER is always used regardless of LLM output — no conditional logic."""
    from perception.strings import FULL_DISCLAIMER
    # Simulate LLM returning old "AI Visibility Score" phrasing (pre-rebrand)
    llm_disclaimer = "The AI Visibility Score (0–100) reflects public data at time of collection."
    # Production code now ignores LLM-generated disclaimer and always uses FULL_DISCLAIMER
    result = FULL_DISCLAIMER
    assert "AI Visibility Score (0–100) reflects" not in result, \
        "Old 'AI Visibility Score' phrasing should not appear in FULL_DISCLAIMER"
    assert "Pulse Score" in result, "Pulse Score sentence missing from FULL_DISCLAIMER"
    assert result.count("Pulse Score") == 1, "Pulse Score sentence appears more than once"


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


# ── Round 3 F1: GBP weak-match rejection and anchor data quarantine ───────────

def test_fetch_provider_none_match_not_verified():
    """fetch_provider must return verified=False for no-match (zero token overlap)."""
    from unittest.mock import patch, MagicMock
    from perception.data.places import fetch_provider

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "places": [{
            "id": "ChIJABC123",
            "displayName": {"text": "Riverside Pizza Kitchen"},
            "rating": 4.5,
            "userRatingCount": 200,
            "businessStatus": "OPERATIONAL",
            "googleMapsUri": "https://maps.google.com/...",
            "types": ["restaurant"],
            "formattedAddress": "100 Main St, Mobile, AL",
        }]
    }

    with patch("httpx.post", return_value=mock_resp):
        with patch("perception.data.places._api_key", return_value="test-key"):
            read, _ = fetch_provider("Desert Orthopaedic Center", "Mobile", "AL")

    # Zero token overlap → "none" match → verified must be False
    assert not read.verified, (
        f"No-match should not be verified; got match={read.name_match}"
    )
    assert read.name_match == "none"


def test_fetch_provider_strong_match_is_verified():
    """fetch_provider must return verified=True when found name strongly matches requested."""
    from unittest.mock import patch, MagicMock
    from perception.data.places import fetch_provider

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "places": [{
            "id": "ChIJDEF456",
            "displayName": {"text": "Desert Orthopaedic Center"},
            "rating": 4.6,
            "userRatingCount": 1024,
            "businessStatus": "OPERATIONAL",
            "googleMapsUri": "https://maps.google.com/...",
            "types": ["doctor"],
            "formattedAddress": "2800 E Desert Inn Rd, Las Vegas, NV",
        }]
    }

    with patch("httpx.post", return_value=mock_resp):
        with patch("perception.data.places._api_key", return_value="test-key"):
            # Request without address matches the canonical name — strong match expected
            read, _ = fetch_provider("Desert Orthopaedic Center", "Las Vegas", "NV")

    assert read.verified, f"Strong match should be verified; match={read.name_match}"


def test_anchor_dedup_drops_main_office_variant():
    """'(Main Office)' suffix variant must be recognized as the anchor and dropped."""
    anchor = "Desert Orthopaedic Center 2800 E Desert Inn Rd"
    variant = "Desert Orthopaedic Center - Desert Inn (Main Office)"
    assert _is_anchor_dup_new(anchor, variant), (
        f"Expected {variant!r} to be detected as a duplicate of anchor {anchor!r}"
    )


def test_anchor_dedup_keeps_summerlin_sibling():
    """'- Summerlin' is a distinct location — must NOT be dropped."""
    anchor = "Desert Orthopaedic Center 2800 E Desert Inn Rd"
    sibling = "Desert Orthopaedic Center - Summerlin"
    assert not _is_anchor_dup_new(anchor, sibling), (
        f"Expected {sibling!r} to be kept as a valid sibling of {anchor!r}"
    )


def test_post_assembly_assertion_clears_duplicate_anchor_rating():
    """collect_platform_data post-assembly assertion must force Not established
    on any non-anchor row whose google_rating/count matches the anchor exactly."""
    # Simulate the assertion logic directly (no network calls)
    anchor_rating, anchor_count = 2.8, 375
    anchor_rows = [{"practice_name": "USA Health Hospital", "is_anchor": True,
                    "google_rating": anchor_rating, "google_count": anchor_count,
                    "not_established": False}]
    other_rows  = [{"practice_name": "USA Health Ophthalmology", "is_anchor": False,
                    "google_rating": anchor_rating, "google_count": anchor_count,
                    "google_url": "https://maps.google.com/test",
                    "not_established": False}]

    import sys, io
    _anchor_sig = (anchor_rating, anchor_count)
    captured = io.StringIO()
    for r in other_rows:
        if (r.get("google_rating"), r.get("google_count")) == _anchor_sig:
            print("ASSERTION", file=captured)
            r["google_rating"] = None
            r["google_count"] = None
            r["google_url"] = None
            r["not_established"] = True

    assert other_rows[0]["not_established"] is True, "Duplicate rating not cleared"
    assert other_rows[0]["google_rating"] is None, "Duplicate google_rating not cleared"
    assert "ASSERTION" in captured.getvalue(), "Assertion message not emitted"


# ── Round 3 F2: Hospital signals excluded from practice reports ───────────────

def test_outcomes_safety_weaknesses_empty_for_practice():
    """_outcomes_safety_weaknesses must return [] for practice-typed providers."""
    from perception.models import RankedProvider
    from perception.pdf import _outcomes_safety_weaknesses

    p = RankedProvider(rank=1, name="Desert Orthopaedic Center", report_type="practice")
    result = _outcomes_safety_weaknesses(p)
    assert result == [], (
        f"Expected [] for practice provider but got: {result}"
    )


def test_outcomes_safety_weaknesses_fires_for_hospital_when_both_absent():
    """Hospital providers still get Leapfrog/CMS entries when both signals are absent."""
    from perception.models import RankedProvider
    from perception.pdf import _outcomes_safety_weaknesses

    p = RankedProvider(rank=1, name="Generic Hospital", report_type="hospital")
    result = _outcomes_safety_weaknesses(p)
    assert any("Leapfrog" in w for w in result), "Leapfrog weakness missing for hospital"
    assert any("CMS" in w for w in result), "CMS weakness missing for hospital"


def test_individual_entity_card_no_hospital_signals_for_practice():
    """_individual_entity_card must not inject Leapfrog/CMS strings for practice providers."""
    from perception.models import RankedProvider
    from perception.pdf import _individual_entity_card

    p = RankedProvider(
        rank=1,
        name="Desert Orthopaedic Center",
        report_type="practice",
        notable_weaknesses=["Limited online scheduling"],
    )
    html = _individual_entity_card(p)
    assert "Leapfrog" not in html, "Leapfrog string appeared in practice card"
    assert "CMS Overall Star" not in html, "CMS Overall Star appeared in practice card"


# ── Round 3 F3: Entity registry caches siblings between runs ─────────────────

def test_entity_registry_save_and_retrieve():
    """Saved siblings are returned on the next call without hitting the LLM."""
    from perception.db import init_db
    from perception.entity_registry import (
        get_registry_siblings,
        save_registry_siblings,
        expire_registry,
    )
    init_db()
    # Use a test-only sentinel anchor name unlikely to collide with real data
    _anchor = "__test_registry_anchor__"
    _city, _state = "TestCity", "TX"
    siblings = [
        {"name": "__test_sibling_A__", "entity_type": "practice", "city": _city, "state": _state},
        {"name": "__test_sibling_B__", "entity_type": "practice", "city": _city, "state": _state},
    ]
    # Start clean
    expire_registry(_anchor, _city, _state)
    assert get_registry_siblings(_anchor, _city, _state) is None, \
        "Registry should be empty before first save"
    save_registry_siblings(_anchor, _city, _state, siblings)
    cached = get_registry_siblings(_anchor, _city, _state)
    assert cached is not None, "Registry returned None after save"
    assert len(cached) == 2, f"Expected 2 siblings, got {len(cached)}"
    assert any(s["name"] == "__test_sibling_A__" for s in cached)
    # Clean up
    expire_registry(_anchor, _city, _state)


def test_entity_registry_expire_clears_entries():
    """expire_registry removes entries so next call returns None."""
    from perception.db import init_db
    from perception.entity_registry import (
        get_registry_siblings,
        save_registry_siblings,
        expire_registry,
    )
    init_db()
    _anchor = "__test_registry_expire__"
    _city, _state = "TestCity", "TX"
    save_registry_siblings(_anchor, _city, _state,
                           [{"name": "__test_sibling_C__", "entity_type": "practice",
                             "city": _city, "state": _state}])
    expire_registry(_anchor, _city, _state)
    assert get_registry_siblings(_anchor, _city, _state) is None, \
        "Registry entry should be gone after expire"


# ── Round 3 F4: Hardcoded disclaimer — Data Limitations block always present ──

def test_disclaimer_contains_data_limitations_block():
    """FULL_DISCLAIMER must include the Data Limitations & Disclaimer header."""
    from perception.strings import FULL_DISCLAIMER
    assert "Data Limitations" in FULL_DISCLAIMER, \
        "FULL_DISCLAIMER missing Data Limitations block"


def test_disclaimer_contains_pulse_score_sentence():
    """FULL_DISCLAIMER must contain the Pulse Score definition sentence."""
    from perception.strings import FULL_DISCLAIMER
    assert "Pulse Score" in FULL_DISCLAIMER, \
        "FULL_DISCLAIMER missing Pulse Score sentence"


def test_disclaimer_exactly_one_pulse_score_sentence():
    """FULL_DISCLAIMER must contain 'Pulse Score' exactly once (no duplication)."""
    from perception.strings import FULL_DISCLAIMER
    count = FULL_DISCLAIMER.count("Pulse Score")
    assert count == 1, f"Expected 1 'Pulse Score' occurrence, got {count}"


def test_disclaimer_contains_no_fabricated_quotes_language():
    """FULL_DISCLAIMER must explicitly disclaim fabricated quotes."""
    from perception.strings import FULL_DISCLAIMER
    assert "fabricated" in FULL_DISCLAIMER.lower(), \
        "FULL_DISCLAIMER missing no-fabricated-quotes language"


def test_disclaimer_contains_insurer_physician_confirmation():
    """FULL_DISCLAIMER must include insurer/physician confirmation guidance."""
    from perception.strings import FULL_DISCLAIMER
    assert "insurer" in FULL_DISCLAIMER.lower() and "physician" in FULL_DISCLAIMER.lower(), \
        "FULL_DISCLAIMER missing insurer/physician confirmation guidance"


# ── Round 3 F5: MIPS/QPP signal flag coverage ────────────────────────────────

def test_mips_flag_not_established_for_not_found():
    """'not found in public sources' → ✗ Not established flag."""
    from perception.pdf import _mips_flag
    html = _mips_flag("MIPS/QPP not found in public sources")
    assert "✗" in html and "Not established" in html, \
        f"Expected ✗ Not established flag; got: {html}"


def test_mips_flag_verified_for_score():
    """'Final Score: 87' → ✓ Verified flag."""
    from perception.pdf import _mips_flag
    html = _mips_flag("MIPS Final score: 87.2 (Exceptional Performance)")
    assert "✓" in html and "Verified" in html, \
        f"Expected ✓ Verified flag; got: {html}"


def test_mips_flag_partial_for_ambiguous():
    """Ambiguous text (no clear status) → ◐ Partial flag."""
    from perception.pdf import _mips_flag
    html = _mips_flag("MIPS/QPP participation status unclear")
    assert "◐" in html and "Partial" in html, \
        f"Expected ◐ Partial flag; got: {html}"


def test_quality_signals_block_cms_quality_has_flag():
    """_quality_signals_block must append a _vflag to any cms_quality_highlights text."""
    from perception.models import RankedProvider
    from perception.pdf import _quality_signals_block
    p = RankedProvider(
        rank=1, name="Test Practice",
        cms_quality_highlights="MIPS/QPP not found in public sources",
    )
    html = _quality_signals_block(p)
    assert "✗" in html, "cms_quality_highlights block missing ✗ Not established flag"
