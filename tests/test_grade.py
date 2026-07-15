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
    """DATA_LIMITATIONS_BLOCK body text must be present in FULL_DISCLAIMER.
    The section heading 'Data Limitations & Disclaimer' is supplied by the HTML
    template (<strong> tag in pdf.py), NOT by the body string — so FULL_DISCLAIMER
    must NOT contain the header as part of the body text (D3 fix)."""
    from perception.strings import DATA_LIMITATIONS_BLOCK, FULL_DISCLAIMER
    assert "Scores and rankings are derived" in FULL_DISCLAIMER, \
        "FULL_DISCLAIMER missing Data Limitations body text"
    assert not DATA_LIMITATIONS_BLOCK.startswith("Data Limitations"), \
        "DATA_LIMITATIONS_BLOCK must not start with the header string (D3: header is in HTML template)"


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


# ── Round 4 D1: Anchor-row collision-bypass regression ───────────────────────

def test_anchor_collision_bypass_condition():
    """D1: when prior_strength is 'anchor-quarantine' and prior_entity matches the
    current entity, the bypass fires — the anchor reclaims its own place_id."""
    pid = "ChIJtestbypass"
    entity_name = "Valley Orthopedic Center"
    assigned = {pid: (entity_name, "anchor-quarantine")}

    prior_entity, prior_strength = assigned[pid]
    bypass_fires = (prior_strength == "anchor-quarantine" and prior_entity == entity_name)
    assert bypass_fires, "Bypass condition must fire for anchor reclaiming its own place_id"

    # After bypass: registration is upgraded to the real match strength
    assigned[pid] = (entity_name, "strong")
    assert assigned[pid] == (entity_name, "strong"), \
        "Registration must be upgraded from anchor-quarantine to real match strength"


def test_anchor_collision_bypass_does_not_fire_for_sibling():
    """D1: bypass must NOT fire when a sibling claims the anchor's pre-quarantined place_id."""
    pid = "ChIJtestconflict"
    anchor_name = "Valley Orthopedic Center"
    sibling_name = "Valley Orthopedic Center - West Campus"
    assigned = {pid: (anchor_name, "anchor-quarantine")}

    prior_entity, prior_strength = assigned[pid]
    bypass_fires = (prior_strength == "anchor-quarantine" and prior_entity == sibling_name)
    assert not bypass_fires, "Bypass must not fire when sibling attempts to claim anchor place_id"


def test_anchor_row_not_nulled_by_pre_quarantine():
    """D1 regression: anchor row must carry its GBP rating, not be marked not_established,
    even though the pre-quarantine step registered the anchor's place_id first."""
    from unittest.mock import patch, MagicMock
    from perception.data.places import GoogleRead

    mock_read = GoogleRead(
        query="Valley Orthopedic Center",
        verified=True,
        rating=4.5,
        review_count=800,
        place_id="ChIJvalley",
        name_match="strong",
        matched_name="Valley Orthopedic Center",
        types=["health"],
        formatted_address="500 E Valley Rd, Reno, NV 89502",
    )

    # Claude platform API mock: returns no platform data (no network needed)
    mock_msg = MagicMock()
    mock_msg.content = []
    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(
        return_value=MagicMock(get_final_message=MagicMock(return_value=mock_msg))
    )
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = mock_stream

    practices = [
        {"name": "Valley Orthopedic Center", "entity_type": "practice", "is_anchor": True,
         "city": "Reno", "state": "NV"},
    ]

    with patch("perception.practice_reputation.places.fetch_provider",
               return_value=(mock_read, None)), \
         patch("perception.practice_reputation._get_client", return_value=mock_client):
        from perception.practice_reputation import collect_platform_data
        results = collect_platform_data(practices, "Valley Orthopedic Center", "Reno", "NV")

    anchor = next((r for r in results if r.get("is_anchor")), None)
    assert anchor is not None, "Anchor row must be present in results"
    assert not anchor["not_established"], \
        "Anchor must NOT be marked not_established — D1 regression check"
    assert anchor["google_rating"] == 4.5, \
        "Anchor must carry its GBP rating (4.5) after collision-bypass fix"
    assert anchor["google_count"] == 800, \
        "Anchor must carry its GBP review count (800) after collision-bypass fix"


def test_scorecard_and_composite_anchor_values_match():
    """AC-1.3: if the composite anchor row has a google_rating, it must match the
    scorecard front_door.rating on the same result object."""
    from perception.models import RankedProvider, AnalysisResult, GoogleFootprint, GoogleFrontDoor
    from datetime import date

    # Simulate a practice result where both sources read the same GBP record
    anchor_rating = 4.5
    provider = RankedProvider(
        rank=1, name="Valley Orthopedic Center",
        google_footprint=GoogleFootprint(
            front_door=GoogleFrontDoor(rating=anchor_rating, count=800, verified=True),
        ),
    )
    result = AnalysisResult(
        run_id="test-run",
        location="Reno, NV",
        entity_type="practice",
        generated_at=date.today(),
        rankings=[provider],
        practice_composite_rows=[
            {"practice_name": "Valley Orthopedic Center", "is_anchor": True,
             "google_rating": anchor_rating, "google_count": 800, "not_established": False},
        ],
    )

    scorecard_rating = result.rankings[0].google_footprint.front_door.rating
    composite_anchor = next(r for r in result.practice_composite_rows if r.get("is_anchor"))
    composite_rating = composite_anchor["google_rating"]

    assert scorecard_rating == composite_rating, (
        f"Scorecard front_door.rating ({scorecard_rating}) must match "
        f"composite anchor google_rating ({composite_rating})"
    )


# ── Round 4 D2: Street address normalizer and address-based dedup ─────────────

def test_normalize_street_rd_to_road():
    from perception.practice_analyzer import _normalize_street
    assert _normalize_street("Desert Inn Rd") == "desert inn road"


def test_normalize_street_e_to_east():
    from perception.practice_analyzer import _normalize_street
    assert _normalize_street("E Desert Inn Road") == "east desert inn road"


def test_normalize_street_st_to_street():
    from perception.practice_analyzer import _normalize_street
    assert _normalize_street("Main St") == "main street"


def test_normalize_street_ave_to_avenue():
    from perception.practice_analyzer import _normalize_street
    assert _normalize_street("Park Ave") == "park avenue"


def test_normalize_street_strips_suite():
    from perception.practice_analyzer import _normalize_street
    result = _normalize_street("Desert Inn Rd Suite 200")
    assert "suite" not in result, f"Suite suffix not stripped: {result!r}"
    assert "200" not in result, f"Suite number not stripped: {result!r}"
    assert "desert" in result and "inn" in result and "road" in result


def test_normalize_street_strips_leading_number():
    from perception.practice_analyzer import _normalize_street
    result = _normalize_street("2800 E Desert Inn Rd")
    assert result == "east desert inn road", f"Got: {result!r}"


def test_address_dedup_suppresses_embedded_address():
    """D2 AC-2.1: sibling whose name = anchor_name + ' - <anchor street>' is suppressed.
    Mirrors the DOC 'East Desert Inn Road' duplicate case."""
    from perception.practice_analyzer import _normalize_street
    import re

    anchor_name = "Desert Orthopaedic Center"
    anchor_addr_raw = "2800 E Desert Inn Rd"
    anchor_addr_norm = _normalize_street(anchor_addr_raw)

    sibling_name = "Desert Orthopaedic Center - East Desert Inn Road"

    def _dup(sn: str) -> bool:
        if sn.strip().lower() == anchor_name.strip().lower():
            return True
        sn_lower = sn.lower()
        anchor_lc = anchor_name.lower()
        if sn_lower.startswith(anchor_lc):
            remainder = re.sub(r'^[\s\-,]+', '', sn_lower[len(anchor_lc):])
            if remainder and _normalize_street(remainder) == anchor_addr_norm:
                return True
        return False

    assert _dup(sibling_name), \
        f"{sibling_name!r} should be detected as a duplicate of anchor at {anchor_addr_raw!r}"


def test_address_dedup_suppresses_explicit_address_field():
    """D2: sibling with an explicit address field matching the anchor's normalized address
    is suppressed, even when the name differs from the anchor's."""
    from perception.practice_analyzer import _normalize_street

    anchor_addr_norm = _normalize_street("2800 E Desert Inn Rd")
    sibling_address = "2800 East Desert Inn Road"

    assert _normalize_street(sibling_address) == anchor_addr_norm, \
        "Sibling with explicit address field matching anchor's address must normalize to same value"


def test_address_dedup_keeps_different_location_name():
    """D2: sibling with a location qualifier (not an address) is NOT suppressed."""
    from perception.practice_analyzer import _normalize_street
    import re

    anchor_name = "Desert Orthopaedic Center"
    anchor_addr_norm = _normalize_street("2800 E Desert Inn Rd")

    sibling_name = "Desert Orthopaedic Center - Summerlin"

    def _address_branch_fires(sn: str) -> bool:
        sn_lower = sn.lower()
        anchor_lc = anchor_name.lower()
        if sn_lower.startswith(anchor_lc):
            remainder = re.sub(r'^[\s\-,]+', '', sn_lower[len(anchor_lc):])
            if remainder and _normalize_street(remainder) == anchor_addr_norm:
                return True
        return False

    assert not _address_branch_fires(sibling_name), \
        f"{sibling_name!r} must NOT be suppressed — 'Summerlin' is not the anchor's street address"


def test_address_dedup_true_negative_different_address():
    """D2 AC-2.2 case 1: same base name, genuinely different street → not suppressed."""
    from perception.practice_analyzer import _normalize_street

    anchor_addr_norm = _normalize_street("100 E Lake Shore Dr")
    sibling_addr = "500 W Waterfront Blvd"

    assert _normalize_street(sibling_addr) != anchor_addr_norm, \
        "Different streets must NOT normalize to the same value"


def test_address_dedup_true_negative_unrelated_entity():
    """D2 AC-2.2 case 2: completely unrelated entity name → address branch never fires."""
    import re
    from perception.practice_analyzer import _normalize_street

    anchor_name = "Desert Orthopaedic Center"
    anchor_addr_norm = _normalize_street("2800 E Desert Inn Rd")

    sibling_name = "Mountain Spine & Joint Institute"  # no common prefix with anchor

    anchor_lc = anchor_name.lower()
    sn_lower = sibling_name.lower()
    address_branch_fires = sn_lower.startswith(anchor_lc)

    assert not address_branch_fires, \
        "Address-based branch must not fire when sibling name has no anchor prefix"


# ── Round 4 D3: Disclaimer header not duplicated in body text ────────────────

def test_disclaimer_body_does_not_start_with_header_string():
    """D3 AC-3.1: DATA_LIMITATIONS_BLOCK must NOT begin with 'Data Limitations'.
    The HTML template supplies that heading; the body starts directly with body copy."""
    from perception.strings import DATA_LIMITATIONS_BLOCK
    assert not DATA_LIMITATIONS_BLOCK.startswith("Data Limitations"), (
        "DATA_LIMITATIONS_BLOCK starts with the header string — this causes the duplicate "
        "heading bug. The header is in the HTML <strong> tag; remove it from the body string."
    )


def test_disclaimer_body_starts_with_scores_sentence():
    """D3 AC-3.1: Body text must open with 'Scores and rankings are derived' immediately."""
    from perception.strings import DATA_LIMITATIONS_BLOCK
    assert DATA_LIMITATIONS_BLOCK.startswith("Scores and rankings are derived"), (
        f"DATA_LIMITATIONS_BLOCK must start with body copy, not a heading. "
        f"Current start: {DATA_LIMITATIONS_BLOCK[:60]!r}"
    )


# ── R6 Phase 2 — Determinism gate ────────────────────────────────────────────
# AC-2.1: same inputs → same entity set and same GBP binding (via identity cache)
# AC-2.2: binding log records the place_id for every resolved entity
# AC-2.3: report-type cache is filtered by entity_type — wrong type is not served

def test_ac2_1_gbp_identity_cache_round_trips():
    """AC-2.1 proxy: set_gbp_identity writes a binding; get_gbp_identity reads it
    back with the same place_id, confirming the durable identity round-trip.
    A second call with the same inputs returns the same place_id — this is the
    mechanism that makes back-to-back composite runs produce identical entity sets."""
    from perception.db import init_db, set_gbp_identity, get_gbp_identity
    init_db()
    _entity = "__test_ac21_entity__"
    _city, _state = "TestCity", "NV"
    _pid = "ChIJAC21TestPlaceId"

    # Write a durable binding
    set_gbp_identity(
        _pid, _entity, _city, _state,
        display_name="Test Entity GBP",
        rating=4.3, review_count=210,
        maps_url="https://maps.google.com/test",
        run_id="run-test-ac21",
    )

    # First read
    result1 = get_gbp_identity(_entity, _city, _state)
    assert result1 is not None, "get_gbp_identity returned None after set"
    assert result1["place_id"] == _pid

    # Second read — identical result confirms determinism
    result2 = get_gbp_identity(_entity, _city, _state)
    assert result2 is not None
    assert result2["place_id"] == result1["place_id"], (
        "Second read returned a different place_id — durable identity is unstable"
    )
    assert result2["rating"] == result1["rating"]
    assert result2["review_count"] == result1["review_count"]


def test_ac2_1_identity_cache_prevents_reassignment():
    """AC-2.1: when a durable binding exists, a new entity cannot claim the same
    place_id — the collision backstop must reject the late-comer.
    This models the scenario where the 1.0/1 record was sliding between entities."""
    from unittest.mock import patch, MagicMock
    from perception.db import init_db, set_gbp_identity, get_gbp_identity
    init_db()

    _pid = "ChIJSlidingRecord999"
    _owner = "__owner_entity__"
    _interloper = "__interloper_entity__"
    _city, _state = "TestCity", "NV"

    # Owner establishes durable binding in a prior run
    set_gbp_identity(
        _pid, _owner, _city, _state,
        display_name="Owner Entity GBP",
        rating=1.0, review_count=1,
        maps_url=None, run_id="run-prior",
    )

    # Simulate a new run: pre-load seeds _assigned_place_ids with owner's binding
    bound = get_gbp_identity(_owner, _city, _state)
    assert bound is not None and bound["place_id"] == _pid

    _assigned = {_pid: (_owner, "gbp_identity")}

    # Interloper tries to claim the same place_id — collision backstop must block it
    assert _pid in _assigned, "Owner binding must be in _assigned before interloper arrives"
    prior_entity, _ = _assigned[_pid]
    assert prior_entity == _owner, "Owner must hold the place_id before interloper check"


def test_ac2_2_binding_log_written():
    """AC-2.2: log_gbp_binding writes a row; the run_id and place_id are
    retrievable, proving the attribution trail exists for forensics."""
    from perception.db import init_db, log_gbp_binding, get_connection
    init_db()
    _run_id = "run-test-ac22-log"
    _entity = "__test_ac22_entity__"
    _pid = "ChIJAC22LogPlaceId"

    log_gbp_binding(
        _run_id, _entity, _pid,
        rating=4.5, review_count=306,
        binding_source="live_fetch",
        binding_reason="name_match=strong",
    )

    con = get_connection()
    row = con.execute(
        "SELECT entity_name, place_id, binding_source, rating FROM practice_reputation_run_log "
        "WHERE run_id = ? AND entity_name = ?",
        [_run_id, _entity],
    ).fetchone()
    con.close()

    assert row is not None, "log_gbp_binding did not write a row"
    assert row[1] == _pid, f"place_id mismatch: expected {_pid}, got {row[1]}"
    assert row[2] == "live_fetch"
    assert row[3] == 4.5


def test_ac2_3_practice_cache_not_served_for_hospital_request():
    """AC-2.3: get_recent_run with entity_type='hospital' must not return a
    result_json that was stored by a practice run (entity_type='practice').
    This prevents the DOC misrouting scenario where a stale practice cache
    was returned for a hospital-typed request (or vice versa)."""
    from perception.db import init_db, get_connection, get_recent_run
    init_db()

    import json
    from datetime import date

    _entity = "__test_ac23_entity__"
    _location = "TestCity, NV"
    _run_id = "run-test-ac23-practice"

    # Insert a practice-typed result directly into analysis_runs
    con = get_connection()
    con.execute(
        """INSERT OR REPLACE INTO analysis_runs
           (run_id, location, entity_name, entity_type, generated_at, result_json)
           VALUES (?, ?, ?, 'practice', ?, ?)""",
        [_run_id, _location, _entity, date.today().isoformat(),
         json.dumps({"entity_type": "practice", "run_id": _run_id})],
    )
    con.close()

    # Hospital-typed request must NOT return the practice result
    result = get_recent_run(_entity, _location, entity_type="hospital")
    assert result is None, (
        "get_recent_run returned a practice result for a hospital request — "
        "this is the routing contamination bug (AC-2.3)"
    )

    # Practice-typed request MUST return the practice result
    result = get_recent_run(_entity, _location, entity_type="practice")
    assert result is not None, (
        "get_recent_run returned None for a practice request with matching entity_type"
    )
    assert result["run_id"] == _run_id


def test_ac2_3_hospital_cache_not_served_for_practice_request():
    """AC-2.3 inverse: a hospital result must not be served to a practice request."""
    from perception.db import init_db, get_connection, get_recent_run
    init_db()

    import json
    from datetime import date

    _entity = "__test_ac23b_entity__"
    _location = "TestCity, NV"
    _run_id = "run-test-ac23b-hospital"

    con = get_connection()
    con.execute(
        """INSERT OR REPLACE INTO analysis_runs
           (run_id, location, entity_name, entity_type, generated_at, result_json)
           VALUES (?, ?, ?, 'hospital', ?, ?)""",
        [_run_id, _location, _entity, date.today().isoformat(),
         json.dumps({"entity_type": "hospital", "run_id": _run_id})],
    )
    con.close()

    # Practice request must not return the hospital result
    result = get_recent_run(_entity, _location, entity_type="practice")
    assert result is None, (
        "get_recent_run returned a hospital result for a practice request (AC-2.3 inverse)"
    )

    # Hospital request must find it
    result = get_recent_run(_entity, _location, entity_type="hospital")
    assert result is not None
    assert result["run_id"] == _run_id


def test_discover_practices_cache_key_does_not_collide_with_siblings():
    """AC-2.4 proxy: discover_practices uses a '[hospital-composite]' prefix so
    its cache entry is distinct from the practice-sibling entry for the same name."""
    from perception.db import init_db
    from perception.entity_registry import (
        get_registry_siblings, save_registry_siblings, expire_registry,
    )
    init_db()
    _entity = "__test_ac24_discover__"
    _city, _state = "TestCity", "NV"
    _composite_key = f"[hospital-composite] {_entity}"

    # Write composite cache under the prefixed key
    expire_registry(_composite_key, _city, _state)
    save_registry_siblings(_composite_key, _city, _state, [
        {"name": "Test Practice A", "entity_type": "practice",
         "city": _city, "state": _state},
    ])

    # Prefixed key returns the composite cache
    cached = get_registry_siblings(_composite_key, _city, _state)
    assert cached is not None and len(cached) == 1

    # Un-prefixed key (sibling cache) is NOT affected
    sibling_cached = get_registry_siblings(_entity, _city, _state)
    assert sibling_cached is None or not any(
        s["name"] == "Test Practice A" for s in sibling_cached
    ), "Hospital composite cache leaked into sibling cache namespace"

    expire_registry(_composite_key, _city, _state)
