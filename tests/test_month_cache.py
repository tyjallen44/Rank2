"""Calendar-month caching: every report cache is valid for the calendar month it was produced in (US Central)."""
from datetime import date, datetime
from perception import db as D


def test_cache_cutoff_is_first_of_month_for_the_standard_window(monkeypatch):
    monkeypatch.setattr(D, "local_today", lambda: date(2026, 10, 31))
    assert D.cache_cutoff(30) == "2026-10-01"       # a report from the 2nd is still cached on the 31st
    assert D.cache_cutoff(0) == "2026-10-31"        # same-day lock keys on the local day
    assert D.cache_cutoff(90) == "2026-08-02"       # other windows stay rolling
    monkeypatch.setattr(D, "local_today", lambda: date(2026, 11, 1))
    assert D.cache_cutoff(30) == "2026-11-01"       # …and fresh again on the 1st
    monkeypatch.setattr(D, "CACHE_WINDOW", "days")
    assert D.cache_cutoff(30) == "2026-10-02"


def test_month_boundaries_including_december():
    assert D.month_start(date(2026, 2, 28)) == date(2026, 2, 1)
    assert D.next_month_start(date(2026, 2, 28)) == date(2026, 3, 1)
    assert D.next_month_start(date(2026, 12, 15)) == date(2027, 1, 1)


def test_monthly_trends_schedule_lands_on_the_first():
    nxt = D._next_run_at("monthly", datetime(2026, 10, 31, 23, 0))
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 11, 1, 8)
    nxt2 = D._next_run_at("monthly", datetime(2026, 12, 2, 1, 0))
    assert (nxt2.year, nxt2.month, nxt2.day) == (2027, 1, 1)
