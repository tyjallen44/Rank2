"""Rebrand verification tests: Pulse naming, score badge, cover strings, and
strings-module integrity.

Run with: python -m pytest tests/test_rebrand.py -v
No server, Claude API, or network access required.
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── (a) grep assertions — no old brand names in user-visible presentation files ──

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_no_rank2_in_index_html():
    text = _read(os.path.join(_ROOT, "web", "index.html"))
    # Exclude code identifiers, HTTP headers, and auth keys that legitimately
    # contain "rank2" as internal tokens
    user_visible_hits = [
        ln for ln in text.splitlines()
        if "Rank2" in ln
        and not any(x in ln for x in [
            "User-Agent", "rk2", "rank2-session", "rank2-npm", "rk2_",
            "CSRank2", "SalesTeamRank2", "Rank2Marketing", "Rank2HealthApp",
            "//", "/*",
        ])
    ]
    assert user_visible_hits == [], (
        f"Found 'Rank2' in user-visible index.html lines:\n"
        + "\n".join(user_visible_hits[:10])
    )


def test_no_careclimb_in_index_html():
    text = _read(os.path.join(_ROOT, "web", "index.html"))
    hits = [ln for ln in text.splitlines() if "CareClimb" in ln]
    assert hits == [], f"Found 'CareClimb' in index.html:\n" + "\n".join(hits[:5])


def test_no_rank2_in_pdf_py():
    text = _read(os.path.join(_ROOT, "perception", "pdf.py"))
    hits = [ln for ln in text.splitlines()
            if "Rank2" in ln and not ln.strip().startswith("#")]
    assert hits == [], f"Found 'Rank2' in pdf.py:\n" + "\n".join(hits[:5])


def test_no_rank2_in_email_utils():
    text = _read(os.path.join(_ROOT, "perception", "email_utils.py"))
    hits = [ln for ln in text.splitlines()
            if "CareClimb" in ln and not ln.strip().startswith("#")]
    assert hits == [], f"Found 'CareClimb' in email_utils.py:\n" + "\n".join(hits[:5])


# ── (b) snapshot / render checks ─────────────────────────────────────────────

def test_aivs_block_contains_pulse_score():
    from perception.pdf import _aivs_block
    from perception.models import RankedProvider, TierScores, GoogleFootprint, GoogleFrontDoor, SystemAggregate, ThirdPartyAggregate

    p = RankedProvider(
        rank=1, name="Test Hospital", location="Test City, TX",
        ai_visibility_score=74,
        tier_scores=TierScores(
            clinical_outcomes_safety=70, credentials_recognition=80,
            patient_experience_reviews=75, access_fit=70,
        ),
        profile="procedural",
        google_footprint=GoogleFootprint(
            front_door=GoogleFrontDoor(query="x", verified=False, reason="test"),
            system_aggregate=SystemAggregate(available=False),
        ),
        third_party=ThirdPartyAggregate(),
        overall_rating="Good",
    )
    html = _aivs_block(p)
    assert "Pulse Score" in html or "PULSE SCORE" in html.upper(), \
        "Score badge must contain 'Pulse Score'"
    assert "AI Visibility" in html or "AI VISIBILITY" in html.upper(), \
        "Score badge must contain 'AI Visibility' descriptor"
    assert 'class="aivs-sublabel"' in html, \
        "Score badge must use aivs-sublabel div for AI Visibility descriptor"
    assert 'class="aivs-label"' in html, \
        "Score badge must use aivs-label div for Pulse Score label"


def test_individual_cover_eyebrow():
    from perception.strings import COVER_INDIVIDUAL
    assert COVER_INDIVIDUAL == "Pulse Diagnostic"


def test_comparison_cover_eyebrow():
    from perception.strings import COVER_COMPARISON
    assert COVER_COMPARISON == "Pulse Comparison"


def test_cover_report_sub():
    from perception.strings import COVER_REPORT_SUB
    assert COVER_REPORT_SUB == "AI Visibility Report"


def test_market_cover_eyebrow():
    from perception.strings import COVER_MARKET
    assert COVER_MARKET == "Market Pulse"


def test_patient_cover_eyebrow():
    from perception.strings import COVER_PATIENT
    assert COVER_PATIENT == "Patient Pulse"


def test_sidebar_nav_labels_in_html():
    text = _read(os.path.join(_ROOT, "web", "index.html"))
    for label in ("Market Pulse", "Patient Pulse", "Pulse Diagnostic", "Pulse Comparison"):
        assert label in text, f"Nav label '{label}' not found in index.html"


def test_pulse_version_in_html():
    text = _read(os.path.join(_ROOT, "web", "index.html"))
    assert "Pulse Version" in text, "Sidebar version footer must say 'Pulse Version'"
    assert "Rank2 Version" not in text, "Old 'Rank2 Version' must be removed"


def test_section_verdict_label():
    from perception.strings import SECTION_VERDICT
    assert SECTION_VERDICT == "Pulse Verdict"


def test_section_assessment_label():
    from perception.strings import SECTION_ASSESSMENT
    assert "Diagnostic" in SECTION_ASSESSMENT
    assert "Roadmap" in SECTION_ASSESSMENT


# ── (c) regression — disclaimer guard and scoring logic untouched ────────────

def test_aivs_disclaimer_starts_with_pulse_score():
    from perception.strings import AIVS_DISCLAIMER
    assert AIVS_DISCLAIMER.startswith("The Pulse Score (0"), \
        "Disclaimer must start with 'The Pulse Score (0'"


def test_aivs_disclaimer_check_token():
    from perception.strings import AIVS_DISCLAIMER_CHECK
    assert AIVS_DISCLAIMER_CHECK == "Pulse Score"


def test_disclaimer_guard_uses_new_token():
    import inspect
    from perception import analyzer
    src = inspect.getsource(analyzer.analyze_location)
    assert "Pulse Score" in src or "AIVS_DISCLAIMER_CHECK" in src, \
        "analyzer.analyze_location must use updated disclaimer guard token"


def test_disclaimer_guard_practice_uses_new_token():
    import inspect
    from perception import practice_analyzer
    src = inspect.getsource(practice_analyzer.analyze_practice)
    assert "Pulse Score" in src or "_AIVS_DISCLAIMER_CHECK" in src, \
        "practice_analyzer.analyze_practice must use updated disclaimer guard token"


def test_filename_tokens_are_pulse():
    from perception.strings import (
        FILE_INDIVIDUAL, FILE_INDIVIDUAL_SUM, FILE_PATIENT, FILE_COMPARISON_PFX
    )
    assert FILE_INDIVIDUAL == "Pulse-Diagnostic"
    assert FILE_INDIVIDUAL_SUM == "Pulse-Diagnostic-Summary"
    assert FILE_PATIENT == "Patient-Pulse"
    assert FILE_COMPARISON_PFX == "pulse-comparison"


def test_scoring_module_untouched():
    """Verify scoring logic has no Pulse/brand references — it should be brand-agnostic."""
    from perception import scoring
    import inspect
    src = inspect.getsource(scoring)
    assert "Pulse" not in src, "scoring.py must not contain brand strings"


def test_email_brand_is_pulse():
    from perception.strings import EMAIL_BRAND
    assert EMAIL_BRAND == "Pulse"
