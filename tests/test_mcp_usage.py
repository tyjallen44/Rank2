"""Per-caller daily cap for the MCP endpoint (perception/mcp_usage.py).

Run with: python -m pytest tests/test_mcp_usage.py -v
No database, network or API key required.
"""
import sys
import os
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from perception import mcp_usage
from tests._mcp_fakes import install_fake_db

RESERVED_ONE = [("INSERT INTO mcp_daily_usage", [(1,)])]


@pytest.fixture(autouse=True)
def _fresh_schema_flag():
    """ensure_schema() remembers it has run, process-wide. Each test starts from
    a clean slate so the "created once" assertions mean something."""
    mcp_usage.reset_schema_cache()
    yield
    mcp_usage.reset_schema_cache()


# ── (a) Schema ────────────────────────────────────────────────────────────────

def test_schema_created_once(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    mcp_usage.ensure_schema()
    mcp_usage.ensure_schema()
    creates = [s for s in db.statements if "CREATE TABLE IF NOT EXISTS mcp_daily_usage" in s]
    assert len(creates) == 1, f"expected one CREATE, got {len(creates)}"


def test_schema_is_created_lazily_not_at_import(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    assert db.statements == []
    mcp_usage.reserve_run("ae@rldatix.com", 20)
    assert db.ran("CREATE TABLE IF NOT EXISTS mcp_daily_usage")


# ── (b) Reserving ─────────────────────────────────────────────────────────────

def test_first_run_returns_one(monkeypatch):
    install_fake_db(monkeypatch, RESERVED_ONE)
    assert mcp_usage.reserve_run("ae@rldatix.com", 20) == 1


def test_reserve_increments(monkeypatch):
    install_fake_db(monkeypatch, [("INSERT INTO mcp_daily_usage", [(7,)])])
    assert mcp_usage.reserve_run("ae@rldatix.com", 20) == 7


def test_at_cap_raises_and_does_not_increment(monkeypatch):
    # No row comes back: the WHERE on DO UPDATE suppressed the write.
    db = install_fake_db(monkeypatch, [("INSERT INTO mcp_daily_usage", [])])
    with pytest.raises(mcp_usage.DailyCapExceeded) as excinfo:
        mcp_usage.reserve_run("ae@rldatix.com", 20)
    assert excinfo.value.cap == 20
    assert excinfo.value.email == "ae@rldatix.com"
    inserts = [s for s in db.statements if "INSERT INTO mcp_daily_usage" in s]
    assert len(inserts) == 1, "a refused reservation must not retry or write again"
    assert not any("UPDATE mcp_daily_usage" in s for s in db.statements)


def test_reserve_passes_the_cap_as_the_where_bound(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    mcp_usage.reserve_run("ae@rldatix.com", 5)
    _sql, params = [c for c in db.log if "INSERT INTO mcp_daily_usage" in c[0]][0]
    assert params[-1] == 5, "the cap is the WHERE bound, not a client-side comparison"


def test_cap_is_per_email(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    mcp_usage.reserve_run("First.Rep@RLDatix.com", 20)
    mcp_usage.reserve_run("second.rep@rldatix.com", 20)
    emails = [params[0] for sql, params in db.log if "INSERT INTO mcp_daily_usage" in sql]
    assert emails == ["first.rep@rldatix.com", "second.rep@rldatix.com"]


def test_cap_is_per_day(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    mcp_usage.reserve_run("ae@rldatix.com", 20, today=date(2026, 9, 18))
    mcp_usage.reserve_run("ae@rldatix.com", 20, today=date(2026, 9, 19))
    days = [params[1] for sql, params in db.log if "INSERT INTO mcp_daily_usage" in sql]
    assert days == [date(2026, 9, 18), date(2026, 9, 19)]


def test_usage_today_reads_zero_when_no_row(monkeypatch):
    install_fake_db(monkeypatch, [])
    assert mcp_usage.usage_today("ae@rldatix.com") == 0


def test_usage_today_reads_the_row(monkeypatch):
    install_fake_db(monkeypatch, [("SELECT runs FROM mcp_daily_usage", [(12,)])])
    assert mcp_usage.usage_today("ae@rldatix.com") == 12


# ── (b2) The day boundary is UTC, not the host's local clock ──────────────────
#
# The cap refusal promises "resets at midnight UTC" (mcp_server._cap_refusal).
# A host running in a timezone ahead of UTC has already rolled over to
# tomorrow while UTC is still on today; the day key must follow UTC or reps
# east of Greenwich get an early, undocumented reset.

def _fixed_clock(monkeypatch, *, utc_now, local_today):
    """Make datetime.now(tz) return `utc_now` and date.today() return
    `local_today`, inside mcp_usage only. Old code that called date.today()
    for the day key would pick up `local_today`; the fix must not."""
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_now

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return local_today

    monkeypatch.setattr(mcp_usage, "datetime", _FixedDatetime)
    monkeypatch.setattr(mcp_usage, "date", _FixedDate)


def test_reserve_run_uses_utc_date_not_local_date(monkeypatch):
    db = install_fake_db(monkeypatch, RESERVED_ONE)
    # 23:58 UTC on the 16th is already 08:58 on the 17th fourteen hours east.
    _fixed_clock(
        monkeypatch,
        utc_now=datetime(2026, 9, 16, 23, 58, tzinfo=timezone.utc),
        local_today=date(2026, 9, 17),
    )
    mcp_usage.reserve_run("ae@rldatix.com", 20)
    days = [params[1] for sql, params in db.log if "INSERT INTO mcp_daily_usage" in sql]
    assert days == [date(2026, 9, 16)], "day key must follow UTC, not the host's local date"


def test_usage_today_uses_utc_date_not_local_date(monkeypatch):
    db = install_fake_db(monkeypatch, [("SELECT runs FROM mcp_daily_usage", [(3,)])])
    _fixed_clock(
        monkeypatch,
        utc_now=datetime(2026, 9, 16, 23, 58, tzinfo=timezone.utc),
        local_today=date(2026, 9, 17),
    )
    mcp_usage.usage_today("ae@rldatix.com")
    _sql, params = db.log[-1]
    assert params[1] == date(2026, 9, 16), "read must key on the UTC date, not the host's local date"


# ── (c) The cap value ─────────────────────────────────────────────────────────

def test_cap_default_is_twenty():
    assert mcp_usage.daily_cap({}) == mcp_usage.DEFAULT_CAP == 20


def test_cap_env_override():
    assert mcp_usage.daily_cap({"PULSE_MCP_DAILY_CAP": "5"}) == 5


@pytest.mark.parametrize("value", ["twenty", "", "0", "-3", "1.5"])
def test_bad_cap_env_falls_back_to_20(value):
    assert mcp_usage.daily_cap({"PULSE_MCP_DAILY_CAP": value}) == 20


# ── (d) The _translate guard ──────────────────────────────────────────────────

def test_sql_has_no_literal_percent():
    # perception.db._translate DOUBLES a literal '%' when params are present, so
    # a LIKE '%..%' added here would silently stop matching.
    for name in ("_CREATE_SQL", "_RESERVE_SQL", "_COUNT_SQL"):
        assert "%" not in getattr(mcp_usage, name), f"{name} must contain no literal '%'"


def test_reserve_sql_reserves_before_the_run():
    sql = " ".join(mcp_usage._RESERVE_SQL.split())
    assert "ON CONFLICT (email, day) DO UPDATE" in sql
    assert "WHERE mcp_daily_usage.runs < ?" in sql
    assert sql.endswith("RETURNING runs")
