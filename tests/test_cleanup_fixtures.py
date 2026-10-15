"""Golden baseline for the four report-quality fixtures (tests/fixtures/cleanup/*.json, captured
with scripts/capture_fixtures.py). Skipped when the fixtures are not present. The numbers below
are the CURRENT (pre-cleanup) values; a [SCORE] commit that moves one updates it here with the
reason in the commit message."""
import json, pathlib
import pytest

FX = pathlib.Path(__file__).parent / "fixtures" / "cleanup"
KEYS = ["gco", "dmos", "orthosouth", "ibji"]


def _load(key):
    p = FX / f"{key}.json"
    if not p.exists():
        pytest.skip(f"fixture {key} not captured")
    return json.loads(p.read_text())


@pytest.mark.parametrize("key", KEYS)
def test_fixture_loads_and_has_core_fields(key):
    d = _load(key)
    res = d["result"]
    assert res["rankings"], "no ranked provider"
    p = res["rankings"][0]
    assert isinstance(p["ai_visibility_score"], int)
    assert set(p["tier_scores"]) >= {"outcomes_safety", "credentials_recognition", "patient_experience_reviews", "access_fit"}


# Headline values as captured (filled in by scripts/fixture_baseline.py once fixtures exist).
BASELINE = {}


@pytest.mark.parametrize("key", KEYS)
def test_headline_counts_match_baseline(key):
    if key not in BASELINE:
        pytest.skip("baseline not recorded yet")
    d = _load(key)
    from scripts.fixture_replay import headline
    assert headline(d["result"]) == BASELINE[key]
