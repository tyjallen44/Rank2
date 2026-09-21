from perception import scoring


def test_outcomes_band_leapfrog_with_cms():
    assert scoring.outcomes_band("A", 5) == 99
    assert scoring.outcomes_band("A", 3) == 94
    assert scoring.outcomes_band("B", 2) == 77
    assert scoring.outcomes_band("C", None) == 64
    assert scoring.outcomes_band("F", 1) == 26


def test_outcomes_band_cms_only_and_none():
    assert scoring.outcomes_band(None, 4) == 76
    assert scoring.outcomes_band("", 3) == 62
    assert scoring.outcomes_band(None, None) is None
    assert scoring.outcomes_band("not rated", None) is None
