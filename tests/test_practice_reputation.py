"""Unit tests for practice reputation logic (no API calls required).

Run with: python -m pytest tests/test_practice_reputation.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _wavg(pairs):
    from perception.practice_reputation import _weighted_average
    return _weighted_average(pairs)


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


def _fake_google(verified=False, rating=None, count=0, maps_url=None):
    return (MagicMock(verified=verified, rating=rating, review_count=count, maps_url=maps_url), None)


# ── _weighted_average ─────────────────────────────────────────────────────────

def test_weighted_average_single():
    avg, total = _wavg([(4.5, 100)])
    assert avg == pytest.approx(4.5)
    assert total == 100

def test_weighted_average_two_equal_weights():
    avg, total = _wavg([(4.0, 50), (5.0, 50)])
    assert avg == pytest.approx(4.5)
    assert total == 100

def test_weighted_average_unequal_weights():
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
    avg, total = _wavg([(4.0, 100), (5.0, 0)])
    assert avg == pytest.approx(4.0)
    assert total == 100


# ── _strip_tracking ───────────────────────────────────────────────────────────

def test_strip_tracking_removes_utm():
    from perception.practice_reputation import _strip_tracking
    url = "https://www.healthgrades.com/group-directory/practice-123?utm_source=google&utm_medium=cpc"
    result = _strip_tracking(url)
    assert "utm_source" not in result
    assert "utm_medium" not in result
    assert "healthgrades.com/group-directory/practice-123" in result

def test_strip_tracking_removes_fbclid():
    from perception.practice_reputation import _strip_tracking
    url = "https://www.yelp.com/biz/some-clinic?fbclid=abc123"
    result = _strip_tracking(url)
    assert "fbclid" not in result
    assert "yelp.com/biz/some-clinic" in result

def test_strip_tracking_preserves_path_params():
    from perception.practice_reputation import _strip_tracking
    url = "https://www.vitals.com/practice/12345?specialty=cardiology"
    result = _strip_tracking(url)
    assert "specialty=cardiology" in result

def test_strip_tracking_none_input():
    from perception.practice_reputation import _strip_tracking
    assert _strip_tracking(None) is None

def test_strip_tracking_empty_string():
    from perception.practice_reputation import _strip_tracking
    assert _strip_tracking("") is None


# ── URL capture in collect_platform_data ─────────────────────────────────────

def test_collector_captures_google_url(monkeypatch):
    """google_url is populated from places.fetch_provider maps_url when verified."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(
            verified=True, rating=4.2, count=150,
            maps_url="https://maps.google.com/?cid=123456"
        ),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": None, "healthgrades_count": 0, "healthgrades_url": None,
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    assert results[0]["google_url"] == "https://maps.google.com/?cid=123456"
    assert results[0]["primary_url"] == "https://maps.google.com/?cid=123456"


def test_collector_captures_platform_urls(monkeypatch):
    """URLs returned by Claude for non-Google platforms are stored and stripped."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.1, "healthgrades_count": 80,
                "healthgrades_url": "https://www.healthgrades.com/group/test-clinic?utm_source=x",
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    row = results[0]
    assert row["healthgrades_url"] == "https://www.healthgrades.com/group/test-clinic"
    assert "utm_source" not in (row["healthgrades_url"] or "")


def test_collector_null_url_when_not_returned(monkeypatch):
    """If Claude returns no URL for a platform, url field is None."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.0, "healthgrades_count": 50,
                "healthgrades_url": None,
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    assert results[0]["healthgrades_url"] is None


# ── not_established flag ──────────────────────────────────────────────────────

def test_not_established_when_no_platforms(monkeypatch):
    """A practice with no data on any platform is marked not_established and has no primary_url."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    empty_payload = [{"name": "Empty Clinic", "affiliation_verified": True,
                      "healthgrades_rating": None, "healthgrades_count": 0, "healthgrades_url": None,
                      "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                      "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                      "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                      "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(empty_payload))

    results = pr.collect_platform_data(
        [{"name": "Empty Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    row = results[0]
    assert row["not_established"] is True
    assert row["platforms_found"] == 0
    assert row["avg_rating"] is None
    assert row["primary_url"] is None


# ── primary URL fallback order ────────────────────────────────────────────────

def test_primary_url_prefers_google(monkeypatch):
    """When Google URL and another platform URL both exist, Google wins."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(
            verified=True, rating=4.0, count=100,
            maps_url="https://maps.google.com/?cid=999",
        ),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.1, "healthgrades_count": 200,
                "healthgrades_url": "https://www.healthgrades.com/group/test-clinic",
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    assert results[0]["primary_url"] == "https://maps.google.com/?cid=999"


def test_primary_url_falls_back_to_highest_count_platform(monkeypatch):
    """When Google is absent, primary_url = URL of platform with highest review count."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.0, "healthgrades_count": 50,
                "healthgrades_url": "https://www.healthgrades.com/group/test-clinic",
                "vitals_rating": 3.8, "vitals_count": 200,
                "vitals_url": "https://www.vitals.com/doctors/test-clinic",
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    # Vitals has 200 reviews vs Healthgrades' 50 — Vitals URL should be primary
    assert results[0]["primary_url"] == "https://www.vitals.com/doctors/test-clinic"


def test_primary_url_none_when_no_urls_available(monkeypatch):
    """When all URLs are None but data exists, primary_url is None (renders unlinked)."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.0, "healthgrades_count": 50, "healthgrades_url": None,
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    assert results[0]["primary_url"] is None


# ── sorting ───────────────────────────────────────────────────────────────────

def test_sort_by_total_reviews_desc(monkeypatch):
    """Practices with more reviews come first; not_established last."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(verified=False),
    )
    claude_payload = [
        {"name": "Small Clinic", "affiliation_verified": True,
         "healthgrades_rating": 4.0, "healthgrades_count": 20, "healthgrades_url": None,
         "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
         "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
         "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
         "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None},
        {"name": "Big Clinic", "affiliation_verified": True,
         "healthgrades_rating": 4.5, "healthgrades_count": 200, "healthgrades_url": None,
         "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
         "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
         "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
         "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None},
        {"name": "Empty Clinic", "affiliation_verified": True,
         "healthgrades_rating": None, "healthgrades_count": 0, "healthgrades_url": None,
         "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
         "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
         "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
         "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None},
    ]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(claude_payload))

    results = pr.collect_platform_data(
        [{"name": "Small Clinic", "city": "Mobile", "state": "AL"},
         {"name": "Big Clinic", "city": "Mobile", "state": "AL"},
         {"name": "Empty Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    names = [r["practice_name"] for r in results]
    assert names[0] == "Big Clinic"
    assert names[1] == "Small Clinic"
    assert names[2] == "Empty Clinic"
    assert results[2]["not_established"] is True


# ── avg_rating formatting ─────────────────────────────────────────────────────

def test_avg_rating_one_decimal():
    avg, _ = _wavg([(4.333, 100)])
    assert round(avg, 1) == 4.3


# ── pdf table HTML ────────────────────────────────────────────────────────────

def _make_row(**kwargs):
    defaults = {
        "practice_name": "Test Clinic", "not_established": False,
        "avg_rating": 4.2, "total_reviews": 100, "platforms_found": 1,
        "platforms_list": "Google", "affiliation_verified": True,
        "collection_date": "2026-07-12", "primary_url": None,
        "platform_entries": None,
    }
    return {**defaults, **kwargs}


def test_table_html_practice_name_is_plain_text():
    """Practice name must never be wrapped in an anchor tag."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(primary_url="https://maps.google.com/?cid=123")]
    html = _practice_reputation_table_html(rows)
    assert "Test Clinic" in html
    # primary_url must not appear as an href — it's stored but not rendered
    assert 'href="https://maps.google.com/?cid=123"' not in html


def test_table_html_practice_name_plain_text_even_with_url():
    """primary_url being present still yields no link on the name cell."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(primary_url="https://maps.google.com/?cid=999",
                      platform_entries=[("google", 100, "https://maps.google.com/?cid=999")])]
    html = _practice_reputation_table_html(rows)
    # The Google URL appears in the platforms column, not the name column
    # Verify name cell has no href by checking the td containing the name
    td_segment = html.split("Test Clinic")[0].split("<td")[-1]
    assert "href" not in td_segment


def test_table_html_not_established_entirely_plain():
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(practice_name="Empty Clinic", not_established=True,
                      avg_rating=None, total_reviews=0, platforms_found=0,
                      platforms_list="", primary_url=None)]
    html = _practice_reputation_table_html(rows)
    assert "Not established" in html
    assert "Empty Clinic" in html
    assert "href" not in html


def test_table_html_no_raw_urls_in_text():
    """No URL strings should appear as visible text content in the table."""
    from perception.pdf import _practice_reputation_table_html
    import re
    rows = [_make_row(
        platform_entries=[
            ("google", 100, "https://maps.google.com/?cid=999"),
            ("healthgrades", 50, "https://www.healthgrades.com/group/test"),
        ],
        platforms_found=2,
        platforms_list="Google, Healthgrades",
    )]
    html = _practice_reputation_table_html(rows)
    # Extract text nodes — strip all tags and check no http URL survives
    text_only = re.sub(r"<[^>]+>", " ", html)
    assert "http" not in text_only


def test_table_html_platform_entries_linked():
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(
        platforms_found=2,
        platforms_list="Google, Healthgrades",
        platform_entries=[
            ("google", 100, "https://maps.google.com/?cid=999"),
            ("healthgrades", 50, "https://www.healthgrades.com/group/test"),
        ],
    )]
    html = _practice_reputation_table_html(rows)
    assert 'href="https://maps.google.com/?cid=999"' in html
    assert 'href="https://www.healthgrades.com/group/test"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


def test_table_html_platform_unlinked_when_no_url():
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(
        platforms_found=1,
        platforms_list="Healthgrades",
        platform_entries=[("healthgrades", 50, None)],
    )]
    html = _practice_reputation_table_html(rows)
    assert "Healthgrades" in html
    assert "healthgrades.com" not in html
    # Only one href maximum (none here since name is plain and platform has no url)
    assert "href" not in html


def test_table_html_unverified_affiliation():
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(affiliation_verified=False)]
    html = _practice_reputation_table_html(rows)
    assert "unverified affiliation" in html


def test_table_html_empty_rows():
    from perception.pdf import _practice_reputation_table_html
    assert _practice_reputation_table_html([]) == ""


def test_table_html_staleness_note():
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row()]
    html = _practice_reputation_table_html(rows, run_date="2026-07-12")
    assert "90" in html


def test_table_html_no_print_url_footnotes():
    """The @media print URL-appending CSS must not be present."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row()]
    html = _practice_reputation_table_html(rows)
    assert 'attr(href)' not in html
    assert '@media print' not in html


def test_csv_url_columns_present_in_result_dict(monkeypatch):
    """URL fields are retained in the collected data dict (CSV/DB path)."""
    from perception import practice_reputation as pr

    monkeypatch.setattr(
        "perception.data.places.fetch_provider",
        lambda *a, **kw: _fake_google(
            verified=True, rating=4.0, count=50,
            maps_url="https://maps.google.com/?cid=abc",
        ),
    )
    payload = [{"name": "Test Clinic", "affiliation_verified": True,
                "healthgrades_rating": 4.1, "healthgrades_count": 30,
                "healthgrades_url": "https://www.healthgrades.com/group/test",
                "vitals_rating": None, "vitals_count": 0, "vitals_url": None,
                "webmd_rating": None, "webmd_count": 0, "webmd_url": None,
                "yelp_rating": None, "yelp_count": 0, "yelp_url": None,
                "ratemds_rating": None, "ratemds_count": 0, "ratemds_url": None}]
    monkeypatch.setattr(pr, "_get_client", lambda: _make_stream_mock(payload))

    results = pr.collect_platform_data(
        [{"name": "Test Clinic", "city": "Mobile", "state": "AL"}],
        "Test Hospital", "Mobile", "AL",
    )
    row = results[0]
    for col in ("google_url", "healthgrades_url", "vitals_url",
                "webmd_url", "yelp_url", "ratemds_url", "primary_url"):
        assert col in row, f"URL column '{col}' missing from result dict"
    assert row["google_url"] == "https://maps.google.com/?cid=abc"
    assert row["healthgrades_url"] == "https://www.healthgrades.com/group/test"


# ── Phase 4: Specialty Practice anchor row ────────────────────────────────────

def _make_anchor_row(**kwargs):
    defaults = {
        "practice_name": "Anchor Clinic", "not_established": False,
        "avg_rating": 4.5, "total_reviews": 200, "platforms_found": 2,
        "platforms_list": "Google, Healthgrades", "affiliation_verified": True,
        "collection_date": "2026-07-12", "primary_url": None,
        "platform_entries": None, "is_anchor": True, "entity_type": "practice",
    }
    return {**defaults, **kwargs}


def test_anchor_row_pinned_first_regardless_of_reviews():
    """_practice_reputation_table_html pins the anchor row first even if it has fewer reviews."""
    from perception.pdf import _practice_reputation_table_html
    anchor = _make_anchor_row(total_reviews=10)
    sibling = _make_row(practice_name="High Sibling", total_reviews=999)
    # Pass anchor AFTER the high-review sibling — renderer must still pin anchor first
    html = _practice_reputation_table_html([sibling, anchor])
    anchor_pos = html.index("Anchor Clinic")
    sibling_pos = html.index("High Sibling")
    assert anchor_pos < sibling_pos


def test_anchor_row_html_has_teal_background():
    """Anchor row must render with the teal-tinted background colour."""
    from perception.pdf import _practice_reputation_table_html
    anchor = _make_anchor_row()
    sibling = _make_row(practice_name="Sibling Clinic", total_reviews=50)
    html = _practice_reputation_table_html([anchor, sibling])
    assert "#edf6f7" in html


def test_anchor_row_html_has_analyzed_badge():
    """Anchor row must carry the '(analyzed)' badge text."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_anchor_row()]
    html = _practice_reputation_table_html(rows)
    assert "(analyzed)" in html


def test_anchor_row_html_practice_name_bold():
    """Anchor row practice name must be rendered with bold weight."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_anchor_row()]
    html = _practice_reputation_table_html(rows)
    # PDF uses a span with font-weight:700 (not a <strong> tag)
    assert 'font-weight:700' in html
    assert "Anchor Clinic" in html


def test_no_affiliates_shows_informational_row():
    """When anchor is sole row (no siblings), an informational 'No affiliated entities' row appears."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_anchor_row()]
    html = _practice_reputation_table_html(rows)
    assert "No affiliated entities established" in html


def test_informational_row_absent_when_siblings_present():
    """The 'No affiliated entities' row must NOT appear when siblings exist."""
    from perception.pdf import _practice_reputation_table_html
    anchor = _make_anchor_row()
    sibling = _make_row(practice_name="Sibling Clinic")
    html = _practice_reputation_table_html([anchor, sibling])
    assert "No affiliated entities established" not in html


def test_hospital_table_no_anchor_treatment():
    """Hospital-anchored tables (no is_anchor rows) must not show anchor styling or informational row."""
    from perception.pdf import _practice_reputation_table_html
    rows = [
        _make_row(practice_name="Practice A", total_reviews=200),
        _make_row(practice_name="Practice B", total_reviews=100),
    ]
    html = _practice_reputation_table_html(rows)
    assert "(analyzed)" not in html
    assert "#edf6f7" not in html
    assert "No affiliated entities established" not in html


def test_non_anchor_rows_sorted_desc_below_anchor():
    """Non-anchor rows sort by total_reviews desc, all below the anchor row."""
    from perception.pdf import _practice_reputation_table_html
    anchor = _make_anchor_row(total_reviews=50)
    high = _make_row(practice_name="High Reviews", total_reviews=500)
    low = _make_row(practice_name="Low Reviews", total_reviews=10)
    # Pass in reverse order to ensure sort is applied
    html = _practice_reputation_table_html([low, anchor, high])
    anchor_pos = html.index("Anchor Clinic")
    high_pos = html.index("High Reviews")
    low_pos = html.index("Low Reviews")
    assert anchor_pos < high_pos < low_pos


def test_anchor_intro_text_differs_from_hospital():
    """Practice-anchor tables must use different intro text than hospital-anchor tables."""
    from perception.pdf import _practice_reputation_table_html
    hospital_rows = [_make_row(practice_name="Some Clinic")]
    practice_rows = [_make_anchor_row()]
    hospital_html = _practice_reputation_table_html(hospital_rows)
    practice_html = _practice_reputation_table_html(practice_rows)
    # They must not be identical (different intro copy)
    assert hospital_html != practice_html


# ── Disclaimer ────────────────────────────────────────────────────────────────

_DISCLAIMER_FRAGMENT = "Ratings shown are point-in-time and are not updated in real time."


def test_disclaimer_present_in_basic_table():
    """Disclaimer must appear in every table regardless of anchor/hospital mode."""
    from perception.pdf import _practice_reputation_table_html
    for rows in (
        [_make_row()],                  # hospital-style (no anchor)
        [_make_anchor_row()],           # practice-style (anchor only)
        [_make_anchor_row(), _make_row(practice_name="Sibling")],  # anchor + sibling
    ):
        html = _practice_reputation_table_html(rows)
        assert _DISCLAIMER_FRAGMENT in html, "Disclaimer missing from table HTML"


def test_disclaimer_appears_before_table_tag():
    """Disclaimer paragraph must come before the <table> element."""
    from perception.pdf import _practice_reputation_table_html
    html = _practice_reputation_table_html([_make_row()])
    disclaimer_pos = html.index(_DISCLAIMER_FRAGMENT)
    table_pos = html.index("<table")
    assert disclaimer_pos < table_pos


def test_disclaimer_uses_oldest_date_with_mixed_dates():
    """When rows have different collection dates the disclaimer shows the oldest."""
    from perception.pdf import _practice_reputation_table_html
    newer = _make_row(practice_name="Newer", collection_date="2026-07-13")
    older = _make_row(practice_name="Older", collection_date="2026-06-01")
    html = _practice_reputation_table_html([newer, older])
    assert "2026-06-01" in html
    assert "data collected as of" in html


def test_disclaimer_oldest_date_includes_physician_sub_rows():
    """Oldest date must consider physician sub-row collection dates too."""
    from perception.pdf import _practice_reputation_table_html
    row = _make_row(collection_date="2026-07-10", physicians=[
        {"physician_name": "Dr. A", "not_established": False,
         "avg_rating": 4.0, "total_reviews": 10, "platforms_found": 1,
         "platforms_list": "Google", "platform_entries": None,
         "collection_date": "2026-05-15"},
    ])
    html = _practice_reputation_table_html([row])
    assert "2026-05-15" in html


def test_disclaimer_no_date_suffix_when_no_dates():
    """When no collection_date is present the disclaimer still renders, just without the suffix."""
    from perception.pdf import _practice_reputation_table_html
    row = _make_row(collection_date="")
    html = _practice_reputation_table_html([row])
    assert _DISCLAIMER_FRAGMENT in html
    assert "data collected as of" not in html


def test_disclaimer_and_staleness_footer_both_present():
    """Both the disclaimer (above table) and the 90-day footer (below table) must coexist."""
    from perception.pdf import _practice_reputation_table_html
    html = _practice_reputation_table_html([_make_row()], run_date="2026-07-13")
    assert _DISCLAIMER_FRAGMENT in html
    assert "90" in html


def test_table_content_unchanged_by_disclaimer():
    """Regression: platform links, ratings, and review counts are unaffected."""
    from perception.pdf import _practice_reputation_table_html
    rows = [_make_row(
        practice_name="Regression Clinic",
        avg_rating=4.5, total_reviews=999,
        platform_entries=[("google", 999, "https://maps.google.com/?cid=1")],
        platforms_found=1,
    )]
    html = _practice_reputation_table_html(rows)
    assert "Regression Clinic" in html
    assert "4.5" in html
    assert "999" in html
    assert 'href="https://maps.google.com/?cid=1"' in html
