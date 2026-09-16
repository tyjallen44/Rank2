"""Per-caller daily cap for the MCP endpoint.

Every rep's run bills the one shared Anthropic and Google Places key, so a
connector that any of a dozen people can fire needs a ceiling per person. This
is that ceiling, modelled on ``count_public_requests_today`` (the existing
per-email cap on the anonymous public flow): one row per (email, UTC day),
counting the tool calls that spend real budget.

The count is RESERVED BEFORE the run starts, in one atomic statement, so two
concurrent tool calls cannot both read "19 used" and both slip under a cap of
20. There is no refund on failure: a run that errors has usually already spent
Anthropic and Places budget, so the counter measures spend, not success.

Deliberately free of MCP and FastAPI imports, and it touches the database
through ``perception.db.get_connection()`` like everything else.
"""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional

TABLE = "mcp_daily_usage"
DEFAULT_CAP = 20
CAP_ENV = "PULSE_MCP_DAILY_CAP"

_CREATE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        email      VARCHAR NOT NULL,
        day        DATE    NOT NULL,
        runs       INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP,
        PRIMARY KEY (email, day)
    )
"""

# Reserve one run, atomically. The WHERE on DO UPDATE is the whole mechanism:
# at the cap it suppresses the write, so RETURNING yields no row and fetchone()
# is None — nothing was incremented and the caller is refused.
#
# perception.db._translate rewrites '?' to '%s' and DOUBLES a literal '%' when
# params are present. This statement contains no '%' and must never grow one
# (no LIKE '%..%' here); test_sql_has_no_literal_percent pins that.
_RESERVE_SQL = f"""
    INSERT INTO {TABLE} (email, day, runs, updated_at)
    VALUES (?, ?, 1, ?)
    ON CONFLICT (email, day) DO UPDATE
       SET runs = {TABLE}.runs + 1, updated_at = EXCLUDED.updated_at
     WHERE {TABLE}.runs < ?
    RETURNING runs
"""

_COUNT_SQL = f"SELECT runs FROM {TABLE} WHERE email = ? AND day = ?"

_schema_ready = False
_schema_lock = threading.Lock()


class DailyCapExceeded(Exception):
    """The caller has already used its allowance for the day. Carries the cap
    and the email so the refusal text can name the number without a second
    lookup."""

    def __init__(self, email: str, cap: int) -> None:
        super().__init__(f"{email} has used the daily Pulse limit of {cap} report runs")
        self.email = email
        self.cap = cap


def _connect() -> Any:
    """The one database seam in this module. Imported inside the function like
    every other database call in this codebase, so nothing connects at import
    time."""
    from perception.db import get_connection
    return get_connection()


def ensure_schema(con: Any = None) -> None:
    """Create the usage table if it is not there yet, once per process.

    Called lazily from reserve_run/usage_today rather than at import or at mount:
    the mount happens at ``server.py`` import time, when the database may be
    unreachable, and his own code only ever calls ``init_db()`` inside a
    handler."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        own = con is None
        con = con or _connect()
        try:
            con.execute(_CREATE_SQL)
        finally:
            if own:
                con.close()
        _schema_ready = True


def reset_schema_cache() -> None:
    """Forget that the table has been created. Test seam only."""
    global _schema_ready
    with _schema_lock:
        _schema_ready = False


def daily_cap(env: Optional[Mapping[str, str]] = None) -> int:
    """Metered tool calls allowed per email per UTC day.

    A missing, non-integer or below-1 value falls back to the default and says
    so: a cap of 0 or "twenty" in the environment would otherwise lock out every
    rep with no explanation."""
    src = env if env is not None else os.environ
    raw = (src.get(CAP_ENV) or "").strip()
    if not raw:
        return DEFAULT_CAP
    try:
        value = int(raw)
    except ValueError:
        print(f"[pulse-mcp-usage] {CAP_ENV}={raw!r} is not an integer; using {DEFAULT_CAP}")
        return DEFAULT_CAP
    if value < 1:
        print(f"[pulse-mcp-usage] {CAP_ENV}={value} is below 1; using {DEFAULT_CAP}")
        return DEFAULT_CAP
    return value


def reserve_run(email: str, cap: Optional[int] = None, *, today: Optional[date] = None) -> int:
    """Claim one run against `email`'s allowance and return the new count.

    Raises DailyCapExceeded when the allowance is already spent, without
    incrementing anything. `today` is injectable for tests; production uses
    the UTC date, matching the refusal text's "resets at midnight UTC" and
    the same day boundary ``count_public_requests_today`` uses. Using the
    server's local date instead would make the reset happen at whatever
    midnight the host's timezone lands on, not the one the caller was told."""
    ensure_schema()
    limit = daily_cap() if cap is None else cap
    day = today or datetime.now(timezone.utc).date()
    key = (email or "").strip().lower()
    con = _connect()
    try:
        row = con.execute(
            _RESERVE_SQL,
            [key, day, datetime.now(timezone.utc), limit],
        ).fetchone()
    finally:
        con.close()
    if not row:
        # The WHERE on DO UPDATE suppressed the write: at the cap, nothing was
        # incremented.
        raise DailyCapExceeded(key, limit)
    return int(row[0])


def usage_today(email: str, *, today: Optional[date] = None) -> int:
    """How many metered runs `email` has already spent today. 0 when it has no
    row yet — an absent row genuinely means an unused day. Uses the UTC date
    for the same reason ``reserve_run`` does: the day boundary must match the
    one the cap refusal promises."""
    ensure_schema()
    day = today or datetime.now(timezone.utc).date()
    key = (email or "").strip().lower()
    con = _connect()
    try:
        row = con.execute(_COUNT_SQL, [key, day]).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0
