"""Shared entity-score cache: the Deep Diagnostic is authoritative; market passes adopt it."""
from types import SimpleNamespace
from perception import practice_analyzer as PA
from perception import analyzer as AN
import perception.db as DB


class _Tiers:
    def __init__(self):
        self.clinical_outcomes_safety = 70; self.credentials_recognition = 70; self.patient_experience_reviews = 70; self.access_fit = 70
    def as_dict(self):
        return dict(self.__dict__)


def _prov(name, score):
    return SimpleNamespace(name=name, ai_visibility_score=score, tier_scores=_Tiers(), overall_rating="B", ai_says="", weighting_profile="practice_procedural", rank=1)


def test_deep_diagnostic_overwrites_a_market_score(monkeypatch):
    puts = []
    monkeypatch.setattr(DB, "get_entity_score", lambda n, l, days=30: {"pulse_score": 61, "tier_scores": {"access_fit": 50}, "source": "market", "weighting_profile": "practice_procedural", "roster_key": ""})
    monkeypatch.setattr(DB, "upsert_entity_score", lambda *a, **k: puts.append((a, k)))
    r = [_prov("Brand Ortho", 77)]
    PA._sync_practice_entity_score(r, "Memphis", "TN", "run1", False, "practice_procedural", "")
    assert r[0].ai_visibility_score == 77                      # the market number was NOT adopted
    assert puts and puts[0][1]["overwrite"] is True and puts[0][1]["source"] == "deep_diagnostic"


def test_deep_diagnostic_adopts_only_an_earlier_deep_diagnostic_of_the_same_roster(monkeypatch):
    puts = []
    monkeypatch.setattr(DB, "get_entity_score", lambda n, l, days=30: {"pulse_score": 74, "tier_scores": {"access_fit": 60}, "source": "deep_diagnostic", "weighting_profile": "practice_procedural", "roster_key": "abc"})
    monkeypatch.setattr(DB, "upsert_entity_score", lambda *a, **k: puts.append((a, k)))
    r = [_prov("Brand Ortho", 77)]
    PA._sync_practice_entity_score(r, "Memphis", "TN", "run1", False, "practice_procedural", "abc")
    assert r[0].ai_visibility_score == 74 and r[0].tier_scores.access_fit == 60 and not puts
    r2 = [_prov("Brand Ortho", 77)]
    PA._sync_practice_entity_score(r2, "Memphis", "TN", "run1", False, "practice_procedural", "other-roster")
    assert r2[0].ai_visibility_score == 77 and puts[-1][1]["overwrite"] is True


def test_market_pass_adopts_the_deep_diagnostic_score(monkeypatch):
    puts = []
    monkeypatch.setattr(DB, "get_entity_score", lambda n, l, days=30: {"pulse_score": 77, "tier_scores": {"access_fit": 59}, "source": "deep_diagnostic", "weighting_profile": "practice_procedural", "roster_key": "abc"} if n == "Brand Ortho" else None)
    monkeypatch.setattr(DB, "upsert_entity_score", lambda *a, **k: puts.append((a, k)))
    r = [_prov("Other Clinic", 80), _prov("Brand Ortho", 61)]
    AN._sync_entity_scores(r, "Memphis", "TN", "practice_procedural", "run2", False, source="market")
    assert [p.name for p in r] == ["Other Clinic", "Brand Ortho"] and r[1].ai_visibility_score == 77 and r[1].tier_scores.access_fit == 59
    assert puts and puts[0][0][0] == "Other Clinic" and puts[0][1]["overwrite"] is False      # market seeds without overwriting
