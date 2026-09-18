"""The reviews pillars must differentiate above 4.5★ and be able to reach 100."""
from perception import scoring, practice_scoring


def test_hospital_top_band_reaches_100():
    assert scoring.experience_band(4.9, 1200) == 98
    assert scoring.experience_band(5.0, 1200) == 98
    assert scoring.experience_band(4.7, 1200) == 94
    assert scoring.experience_band(4.5, 1200) == 90
    assert scoring.experience_band(4.5, 200) == 86          # anchor: 4.5★ → 85+
    assert scoring.experience_band(4.4, 1200) == 81         # 4.0–4.4 band unchanged


def test_hospital_rating_orders_above_4_5():
    assert scoring.experience_band(4.9, 500) > scoring.experience_band(4.7, 500) > scoring.experience_band(4.5, 500)


def test_practice_top_band_reaches_100():
    assert practice_scoring.reviews_band(4.9, 500) == 98
    assert practice_scoring.reviews_band(4.7, 500) == 94
    assert practice_scoring.reviews_band(4.5, 500) == 90
    assert practice_scoring.reviews_band(4.5, 50) == 82     # thin volume still penalised
    assert practice_scoring.reviews_band(4.4, 500) == 79    # lower bands unchanged


def test_practice_rating_orders_above_4_5():
    assert practice_scoring.reviews_band(4.9, 300) > practice_scoring.reviews_band(4.7, 300) > practice_scoring.reviews_band(4.5, 300)
