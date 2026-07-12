"""Unit tests for practice reputation logic (no API calls required).

Run with: python -m pytest tests/test_practice_reputation.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


# ── _weighted_average ─────────────────────────────────────────────────────────

def _wavg(pairs):
    from perception.practice_reputation import _weighted_average
    return _weighted_average(pairs)

def test_weighted_average_single():
    avg, total = _wavg([(4.5, 100)])
    assert avg == pytest.approx(4.5)
    assert total == 100

def test_weighted_average_two_equal_weights():
    avg, total = _wavg([(4.0, 50), (5.0, 50)])
    assert avg == pytest.approx(4.5)
    assert total == 100

def test_weighted_average_unequal_weights():
    # 3.0 with 10 reviews, 5.0 with 90 reviews → (30 + 450) / 100 = 4.8
    avg, total = _wavg([(3.0, 10), (5.0, 90)])
    assert avg == pytest.approx(4.8)
    assert total == 100

def test_weighted_average_empty():
    avg, total = _wavg([])
    assert avg is None
    assert total == 0

def test_weighted_average_zero_reviews():
    avg, total = _wavg([(4.0, 0), (3.5, 0)])
    assert avg is None
    assert total == 0

def test_weighted_average_mixed_zero_nonzero():
    # Only the 4.0/100 pair contributes; zero-count pair is excluded
    avg, total = _wavg([(4.0, 100), (5.0, 0)])
    assert avg == pytest.approx(4.0)
    assert total == 100


# ── not_established flag ──────────────────────────────────────────────────────

def _make_stream_mock(practices_payload: list[dict]):
    """Build a mock client whose messages.stream() context manager returns given practices."""
    final_msg = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "submit_reputation_data"
    tool_block.input = {"practices": practices_payload}
    final_msg.content = [tool_block]

    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=stream_ctx)
    stream_ctx.__exit__ = MagicMock(return_value=False)
    stream_ctx.get_final_message = MagicMock(return_value=final_msg)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = stream_ctx
    return mock_client


def test_not_established_when_no_platforms(monkeypatch):
    """A practice that returns no data on any platform should be marked not_established."""
    from perception import practice_reputation as pr

    monkeypatch.setattr("perception.data.places.fetch_provider", lambda *a, **kw: (MagicMock(verified=False, rating=None, review_count=0), None))

    empty_payload = [{"name": "Empty Clinic", "affiliation_verified": True,
                      "healthgrades_rating": None, "healthgrades_count": 0,
                      "vitals_rating": None, "vitals_count": 0,
                      "webmd_rating": None, "webmd_count": 0,
                      "yelp_rating": None, "yelp_count": 0,
                      "ratemds_rating": None, "ratemds_count": 0}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(empty_payload))

    practices = [{"name": "Empty Clinic", "city": "Mobile", "state": "AL"}]
    results = pr.collect_platform_data(practices, "Test Hospital", "Mobile", "AL")
    assert len(results) == 1
    row = results[0]
    assert row["not_established"] is True
    assert row["platforms_found"] == 0
    assert row["avg_rating"] is None


# ── sorting ───────────────────────────────────────────────────────────────────

def test_sort_by_total_reviews_desc(monkeypatch):
    """Practices with more reviews should come first; not_established last."""
    from perception import practice_reputation as pr

    monkeypatch.setattr("perception.data.places.fetch_provider", lambda *a, **kw: (MagicMock(verified=False, rating=None, review_count=0), None))

    practices_input = [
        {"name": "Small Clinic",   "city": "Mobile", "state": "AL"},
        {"name": "Big Clinic",     "city": "Mobile", "state": "AL"},
        {"name": "Empty Clinic",   "city": "Mobile", "state": "AL"},
    ]

    claude_payload = [
        {"name": "Small Clinic",  "affiliation_verified": True,
         "healthgrades_rating": 4.0, "healthgrades_count": 20,
         "vitals_rating": None, "vitals_count": 0, "webmd_rating": None, "webmd_count": 0,
         "yelp_rating": None, "yelp_count": 0, "ratemds_rating": None, "ratemds_count": 0},
        {"name": "Big Clinic",    "affiliation_verified": True,
         "healthgrades_rating": 4.5, "healthgrades_count": 200,
         "vitals_rating": None, "vitals_count": 0, "webmd_rating": None, "webmd_count": 0,
         "yelp_rating": None, "yelp_count": 0, "ratemds_rating": None, "ratemds_count": 0},
        {"name": "Empty Clinic",  "affiliation_verified": True,
         "healthgrades_rating": None, "healthgrades_count": 0,
         "vitals_rating": None, "vitals_count": 0, "webmd_rating": None, "webmd_count": 0,
         "yelp_rating": None, "yelp_count": 0, "ratemds_rating": None, "ratemds_count": 0},
    ]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(claude_payload))

    results = pr.collect_platform_data(practices_input, "Test Hospital", "Mobile", "AL")
    names = [r["practice_name"] for r in results]
    assert names[0] == "Big Clinic"
    assert names[1] == "Small Clinic"
    assert names[2] == "Empty Clinic"
    assert results[2]["not_established"] is True


# ── avg_rating formatting ─────────────────────────────────────────────────────

def test_avg_rating_one_decimal():
    avg, _ = __import__("perception.practice_reputation", fromlist=["_weighted_average"])._weighted_average(
        [(4.333, 100)]
    )
    # Should round to 1 decimal when formatted
    assert round(avg, 1) == 4.3


# ── pdf table HTML ────────────────────────────────────────────────────────────

def test_practice_reputation_table_html_not_established():
    from perception.pdf import _practice_reputation_table_html
    rows = [{"practice_name": "Empty Clinic", "not_established": True,
             "avg_rating": None, "total_reviews": 0, "platforms_found": 0,
             "platforms_list": "", "affiliation_verified": True,
             "collection_date": "2026-06-29"}]
    html = _practice_reputation_table_html(rows)
    assert "Not established" in html
    assert "Empty Clinic" in html

def test_practice_reputation_table_html_unverified():
    from perception.pdf import _practice_reputation_table_html
    rows = [{"practice_name": "Mystery Clinic", "not_established": False,
             "avg_rating": 4.1, "total_reviews": 50, "platforms_found": 2,
             "platforms_list": "Google, Healthgrades", "affiliation_verified": False,
             "collection_date": "2026-06-29"}]
    html = _practice_reputation_table_html(rows)
    assert "unverified affiliation" in html
    assert "Mystery Clinic" in html

def test_practice_reputation_table_html_empty_rows():
    from perception.pdf import _practice_reputation_table_html
    html = _practice_reputation_table_html([])
    assert html == ""

def test_practice_reputation_table_html_staleness_note():
    from perception.pdf import _practice_reputation_table_html
    rows = [{"practice_name": "Test", "not_established": False,
             "avg_rating": 4.0, "total_reviews": 10, "platforms_found": 1,
             "platforms_list": "Google", "affiliation_verified": True,
             "collection_date": "2026-06-29"}]
    html = _practice_reputation_table_html(rows, run_date="2026-06-29")
    assert "90" in html  # staleness note mentions 90 days
