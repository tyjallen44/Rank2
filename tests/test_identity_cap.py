"""Identity & Machine-Readability ceiling when AI assistants cannot read the site."""
from perception.models import RankedProvider, TierScores
from perception.data.website_facts import identity_cap, apply_identity_cap


def _prov(identity=92, score=80, **kw):
    return RankedProvider(rank=1, name="DMOS", weighting_profile="practice_procedural", report_type="practice",
                          ai_visibility_score=score,
                          tier_scores=TierScores(clinical_outcomes_safety=80, credentials_recognition=92,
                                                 patient_experience_reviews=identity, access_fit=83), **kw)


def test_identity_cap_levels():
    assert identity_cap({"status": "blocked"}) == (45, "AI crawlers are turned away at the firewall")
    assert identity_cap({"status": "measured", "robots_allows_ai": False}) == (55, "robots.txt disallows AI crawlers")
    assert identity_cap({"status": "measured", "robots_allows_ai": True}) is None
    assert identity_cap({"status": "unreachable"}) is None and identity_cap(None) is None


def test_blocked_site_caps_pillar_and_lowers_composite():
    p = _prov(identity=72, score=82)
    info = apply_identity_cap(p, {"status": "blocked"}, "practice_procedural")
    assert info and info["cap"] == 45 and info["pillar_before"] == 72
    assert p.tier_scores.patient_experience_reviews == 45
    # procedural weights: .30/.30/.20/.20 → 24 + 27.6 + 9 + 16.6 = 77
    assert p.ai_visibility_score == 77 and info["score_after"] == 77
    assert p.overall_rating == "Q1"


def test_no_change_when_pillar_already_under_the_cap_or_site_readable():
    p = _prov(identity=40, score=70)
    assert apply_identity_cap(p, {"status": "blocked"}, "practice_procedural") is None
    assert p.tier_scores.patient_experience_reviews == 40 and p.ai_visibility_score == 70
    p2 = _prov()
    assert apply_identity_cap(p2, {"status": "measured", "robots_allows_ai": True}, "practice_procedural") is None


def test_existing_74_ceiling_is_preserved():
    p = _prov(identity=90, score=74, score_ceiling_applied=True, score_ceiling_reason="board certification unverifiable")
    info = apply_identity_cap(p, {"status": "measured", "robots_allows_ai": False}, "practice_procedural")
    assert info["cap"] == 55 and p.tier_scores.patient_experience_reviews == 55
    assert p.ai_visibility_score <= 74 and p.score_ceiling_applied is True
