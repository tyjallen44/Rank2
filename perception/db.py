from __future__ import annotations

import duckdb
from datetime import date, timedelta
from pathlib import Path

from .config import settings


from typing import Any


def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(settings.db_path)


def init_db() -> None:
    con = get_connection()
    con.executemany("", [])  # ensure connection is live
    con.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id          VARCHAR PRIMARY KEY,
            entity_type VARCHAR NOT NULL,
            name        VARCHAR NOT NULL,
            npi         VARCHAR,
            address     VARCHAR,
            city        VARCHAR,
            state       VARCHAR,
            zip         VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            source           VARCHAR NOT NULL,
            entity_id        VARCHAR NOT NULL,
            review_id        VARCHAR NOT NULL,
            author           VARCHAR,
            rating           DOUBLE,
            text             VARCHAR,
            review_date      DATE,
            sentiment        VARCHAR,
            sentiment_score  DOUBLE,
            PRIMARY KEY (source, review_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS entity_summaries (
            entity_id     VARCHAR NOT NULL,
            source        VARCHAR NOT NULL,
            avg_rating    DOUBLE,
            review_count  INTEGER,
            positive_pct  DOUBLE,
            negative_pct  DOUBLE,
            as_of         DATE,
            PRIMARY KEY (entity_id, source)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS analysis_runs (
            run_id              VARCHAR PRIMARY KEY,
            location            VARCHAR NOT NULL,
            specialty           VARCHAR,
            aggregate           BOOLEAN DEFAULT FALSE,
            generated_at        DATE NOT NULL,
            top_recommendation  VARCHAR,
            practical_advice    VARCHAR,
            disclaimer          VARCHAR,
            report_markdown     VARCHAR,
            pdf_path            VARCHAR,
            md_path             VARCHAR
        )
    """)
    # Migrate older DBs that pre-date the path columns
    existing_run_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='analysis_runs'"
    ).fetchall()}
    for col, definition in [
        ("pdf_path", "VARCHAR"),
        ("md_path", "VARCHAR"),
        ("user_role", "VARCHAR"),
        ("aggregate", "BOOLEAN DEFAULT FALSE"),
        ("patient_perspective", "BOOLEAN DEFAULT FALSE"),
        ("teaser_report", "BOOLEAN DEFAULT FALSE"),
        ("individual_report", "BOOLEAN DEFAULT FALSE"),
        ("entity_name", "VARCHAR"),
        # AI Visibility Score additions
        ("weighting_profile", "VARCHAR"),
        ("market_overview", "VARCHAR"),
        ("ai_visibility_verdict", "VARCHAR"),
        ("coverage_note", "VARCHAR"),
        ("entity_type", "VARCHAR DEFAULT 'hospital'"),
        ("rubric_version", "VARCHAR"),
        ("practice_profile", "VARCHAR"),
        ("result_json", "VARCHAR"),
    ]:
        if col not in existing_run_cols:
            con.execute(f"ALTER TABLE analysis_runs ADD COLUMN {col} {definition}")
    # Tag pre-existing rows (before role isolation) as admin
    con.execute("UPDATE analysis_runs SET user_role = 'admin' WHERE user_role IS NULL")
    # Cache for system-wide weighted reputation (ratings move slowly; TTL'd in code).
    con.execute("""
        CREATE TABLE IF NOT EXISTS reputation_cache (
            org_key     VARCHAR PRIMARY KEY,
            payload     VARCHAR,
            fetched_at  DATE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ranked_providers (
            run_id                  VARCHAR NOT NULL,
            rank                    INTEGER NOT NULL,
            name                    VARCHAR NOT NULL,
            affiliation_type        VARCHAR DEFAULT 'unknown',
            size_category           VARCHAR DEFAULT 'unknown',
            physician_count         VARCHAR,
            overall_rating          VARCHAR,
            key_strengths           VARCHAR,
            notable_weaknesses      VARCHAR,
            best_suited_for         VARCHAR,
            recommendation_summary  VARCHAR,
            consolidated_locations  VARCHAR DEFAULT '[]',
            PRIMARY KEY (run_id, rank)
        )
    """)
    # Migrate older DBs
    existing_provider_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='ranked_providers'"
    ).fetchall()}
    for col, definition in [
        ("affiliation_type", "VARCHAR DEFAULT 'unknown'"),
        ("size_category", "VARCHAR DEFAULT 'unknown'"),
        ("physician_count", "VARCHAR"),
        ("consolidated_locations", "VARCHAR DEFAULT '[]'"),
        # AI Visibility Score additions
        ("ai_visibility_score", "INTEGER"),
        ("weighting_profile", "VARCHAR"),
        ("tier_scores", "VARCHAR DEFAULT '{}'"),
        ("google_footprint", "VARCHAR DEFAULT '{}'"),
        ("third_party_aggregate", "VARCHAR DEFAULT '{}'"),
        ("disqualifiers", "VARCHAR DEFAULT '[]'"),
        ("website_url", "VARCHAR"),
        ("patient_voice_summary", "VARCHAR DEFAULT ''"),
        ("leapfrog_grade", "VARCHAR"),
        ("accreditations", "VARCHAR DEFAULT '[]'"),
        ("cms_quality_highlights", "VARCHAR DEFAULT ''"),
        ("cms_star_rating", "INTEGER"),
        ("us_news_rankings", "VARCHAR DEFAULT '[]'"),
        ("ai_says", "VARCHAR DEFAULT ''"),
        ("trauma_level", "VARCHAR"),
        ("teaching_status", "VARCHAR"),
        # Practice Edition derived metrics
        ("entity_resolution_pct",  "DOUBLE"),
        ("linkage_integrity_pct",  "DOUBLE"),
        ("physician_capture_rate", "DOUBLE"),
        ("key_person_flag",        "BOOLEAN DEFAULT FALSE"),
        ("score_ceiling_applied",  "BOOLEAN DEFAULT FALSE"),
        ("score_ceiling_reason",   "VARCHAR"),
    ]:
        if col not in existing_provider_cols:
            con.execute(f"ALTER TABLE ranked_providers ADD COLUMN {col} {definition}")
    # ── SSO auth tables ───────────────────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            VARCHAR PRIMARY KEY,
            email         VARCHAR NOT NULL UNIQUE,
            name          VARCHAR,
            role          VARCHAR DEFAULT 'user',
            auth_type     VARCHAR NOT NULL,
            password_hash VARCHAR,
            password_salt VARCHAR,
            is_active     BOOLEAN DEFAULT TRUE,
            created_at    TIMESTAMP,
            last_login    TIMESTAMP,
            invited_by    VARCHAR,
            brand         VARCHAR DEFAULT 'original'
        )
    """)
    existing_user_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='users'"
    ).fetchall()}
    for col, definition in [
        ("brand", "VARCHAR DEFAULT 'original'"),
    ]:
        if col not in existing_user_cols:
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
    con.execute("UPDATE users SET brand = 'original' WHERE brand IS NULL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS access_requests (
            id           VARCHAR PRIMARY KEY,
            email        VARCHAR NOT NULL,
            name         VARCHAR,
            request_type VARCHAR NOT NULL,
            status       VARCHAR DEFAULT 'pending',
            requested_at TIMESTAMP NOT NULL,
            handled_at   TIMESTAMP,
            handled_by   VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS password_tokens (
            token      VARCHAR PRIMARY KEY,
            user_id    VARCHAR NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used       BOOLEAN DEFAULT FALSE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id           VARCHAR PRIMARY KEY,
            number       INTEGER,
            title        VARCHAR NOT NULL,
            type         VARCHAR NOT NULL,
            body         TEXT NOT NULL,
            submitted_by VARCHAR NOT NULL,
            submitted_at TIMESTAMP NOT NULL,
            action       VARCHAR DEFAULT 'pending',
            notes        VARCHAR DEFAULT ''
        )
    """)
    existing_fb_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='feedback'"
    ).fetchall()}
    for col, definition in [
        ("number", "INTEGER"),
        ("notes",  "VARCHAR DEFAULT ''"),
    ]:
        if col not in existing_fb_cols:
            con.execute(f"ALTER TABLE feedback ADD COLUMN {col} {definition}")
    # Assign sequential numbers to any rows that don't have one yet
    con.execute("""
        UPDATE feedback SET number = sub.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (ORDER BY submitted_at ASC, id ASC) AS rn
            FROM feedback
            WHERE number IS NULL
        ) sub
        WHERE feedback.id = sub.id
    """)
    # ── Down-migrate System Composite (Tier 3) tables ────────────────────────
    for _tbl in ("composite_results", "network_battery_runs",
                 "network_entities", "network_registries"):
        try:
            con.execute(f"DROP TABLE IF EXISTS {_tbl}")
        except Exception:
            pass
    # Drop composite_mode column from analysis_runs if it exists
    try:
        _ar_cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='analysis_runs'"
        ).fetchall()}
        if "composite_mode" in _ar_cols:
            con.execute("ALTER TABLE analysis_runs DROP COLUMN composite_mode")
    except Exception:
        pass

    # ── Practice Composite reputation tables ──────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS practice_reputation_runs (
            id            VARCHAR PRIMARY KEY,
            run_id        VARCHAR NOT NULL,
            collected_at  TIMESTAMP,
            expires_at    TIMESTAMP,
            created_at    TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS practice_reputation_practices (
            id                   VARCHAR PRIMARY KEY,
            rep_run_id           VARCHAR NOT NULL,
            practice_name        VARCHAR NOT NULL,
            city                 VARCHAR,
            state                VARCHAR,
            affiliation_verified BOOLEAN DEFAULT TRUE,
            google_rating        DOUBLE,
            google_count         INTEGER,
            healthgrades_rating  DOUBLE,
            healthgrades_count   INTEGER,
            vitals_rating        DOUBLE,
            vitals_count         INTEGER,
            webmd_rating         DOUBLE,
            webmd_count          INTEGER,
            yelp_rating          DOUBLE,
            yelp_count           INTEGER,
            ratemds_rating       DOUBLE,
            ratemds_count        INTEGER,
            avg_rating           DOUBLE,
            total_reviews        INTEGER DEFAULT 0,
            platforms_found      INTEGER DEFAULT 0,
            platforms_list       VARCHAR DEFAULT '',
            not_established      BOOLEAN DEFAULT FALSE,
            collection_date      DATE,
            created_at           TIMESTAMP
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS practice_reputation_physicians (
            id                   VARCHAR PRIMARY KEY,
            rep_run_id           VARCHAR NOT NULL,
            parent_entity        VARCHAR,
            physician_name       VARCHAR NOT NULL,
            npi                  VARCHAR,
            specialty            VARCHAR,
            credential           VARCHAR,
            not_established      BOOLEAN DEFAULT FALSE,
            avg_rating           DOUBLE,
            total_reviews        INTEGER DEFAULT 0,
            platforms_found      INTEGER DEFAULT 0,
            platforms_list       VARCHAR DEFAULT '',
            collection_date      DATE,
            google_rating        DOUBLE,
            google_count         INTEGER,
            google_url           VARCHAR,
            healthgrades_rating  DOUBLE,
            healthgrades_count   INTEGER,
            healthgrades_url     VARCHAR,
            vitals_rating        DOUBLE,
            vitals_count         INTEGER,
            vitals_url           VARCHAR,
            webmd_rating         DOUBLE,
            webmd_count          INTEGER,
            webmd_url            VARCHAR,
            yelp_rating          DOUBLE,
            yelp_count           INTEGER,
            yelp_url             VARCHAR,
            ratemds_rating       DOUBLE,
            ratemds_count        INTEGER,
            ratemds_url          VARCHAR,
            primary_url          VARCHAR,
            created_at           TIMESTAMP
        )
    """)

    # Migrate practice_reputation_practices to add URL columns (added in v2)
    _pr_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='practice_reputation_practices'"
    ).fetchall()}
    _pr_col_defs = {
        "google_url": "VARCHAR", "healthgrades_url": "VARCHAR",
        "vitals_url": "VARCHAR", "webmd_url": "VARCHAR",
        "yelp_url": "VARCHAR", "ratemds_url": "VARCHAR", "primary_url": "VARCHAR",
        "is_anchor": "BOOLEAN DEFAULT FALSE", "entity_type": "VARCHAR",
    }
    for _col, _def in _pr_col_defs.items():
        if _col not in _pr_cols:
            try:
                con.execute(
                    f"ALTER TABLE practice_reputation_practices ADD COLUMN {_col} {_def}"
                )
            except Exception:
                pass

    con.execute("""
        CREATE TABLE IF NOT EXISTS tracked_entities (
            id           VARCHAR PRIMARY KEY,
            entity_name  VARCHAR NOT NULL,
            city         VARCHAR NOT NULL,
            state        VARCHAR NOT NULL,
            specialty    VARCHAR,
            aggregate    BOOLEAN DEFAULT TRUE,
            schedule     VARCHAR DEFAULT 'monthly',
            last_run_at  TIMESTAMP,
            next_run_at  TIMESTAMP,
            created_by   VARCHAR NOT NULL,
            created_at   TIMESTAMP NOT NULL,
            active       BOOLEAN DEFAULT TRUE,
            notes        VARCHAR
        )
    """)
    con.close()


def set_run_role(run_id: str, role: str) -> None:
    """Tag an analysis run with the role of the user who created it."""
    con = get_connection()
    con.execute("UPDATE analysis_runs SET user_role = ? WHERE run_id = ?", [role, run_id])
    con.close()


def query_history(role: str) -> list[dict[str, Any]]:
    """Return analysis runs for the given role, newest first. Admin sees all roles."""
    con = get_connection()
    if role == "admin":
        rows = con.execute("""
            SELECT
                a.run_id,
                a.location,
                a.specialty,
                a.generated_at,
                a.pdf_path,
                a.md_path,
                COUNT(p.rank) AS provider_count
            FROM analysis_runs a
            LEFT JOIN ranked_providers p ON p.run_id = a.run_id
            GROUP BY a.run_id, a.location, a.specialty, a.generated_at, a.pdf_path, a.md_path
            ORDER BY a.generated_at DESC, a.run_id DESC
        """).fetchall()
    else:
        rows = con.execute("""
            SELECT
                a.run_id,
                a.location,
                a.specialty,
                a.generated_at,
                a.pdf_path,
                a.md_path,
                COUNT(p.rank) AS provider_count
            FROM analysis_runs a
            LEFT JOIN ranked_providers p ON p.run_id = a.run_id
            WHERE a.user_role = ?
            GROUP BY a.run_id, a.location, a.specialty, a.generated_at, a.pdf_path, a.md_path
            ORDER BY a.generated_at DESC, a.run_id DESC
        """, [role]).fetchall()
    cols = ["run_id", "location", "specialty", "generated_at",
            "pdf_path", "md_path", "provider_count"]
    con.close()
    return [dict(zip(cols, row)) for row in rows]


def create_feedback(title: str, ftype: str, body: str, submitted_by: str) -> dict:
    import uuid
    from datetime import datetime
    con = get_connection()
    fid = str(uuid.uuid4())
    now = datetime.utcnow()
    row = con.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM feedback").fetchone()
    next_num = row[0] if row else 1
    con.execute(
        "INSERT INTO feedback (id, number, title, type, body, submitted_by, submitted_at, action, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '')",
        [fid, next_num, title, ftype, body, submitted_by, now]
    )
    con.close()
    return {"id": fid, "number": next_num, "title": title, "type": ftype, "body": body,
            "submitted_by": submitted_by, "submitted_at": str(now), "action": "pending", "notes": ""}


def list_feedback() -> list[dict]:
    con = get_connection()
    rows = con.execute("""
        SELECT id, number, title, type, body, submitted_by, submitted_at, action, notes
        FROM feedback
        ORDER BY submitted_at DESC
    """).fetchall()
    con.close()
    cols = ["id", "number", "title", "type", "body", "submitted_by", "submitted_at", "action", "notes"]
    return [dict(zip(cols, r)) | {"submitted_at": str(r[6])} for r in rows]


def update_feedback(feedback_id: str, **kwargs) -> None:
    allowed = {"title", "type", "body", "action", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    con = get_connection()
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE feedback SET {sets} WHERE id=?", list(fields.values()) + [feedback_id])
    con.close()


def update_feedback_action(feedback_id: str, action: str) -> None:
    con = get_connection()
    con.execute("UPDATE feedback SET action = ? WHERE id = ?", [action, feedback_id])
    con.close()


# ── Tracked Entities ──────────────────────────────────────────────────────────

def _next_run_at(schedule: str, from_dt=None):
    """Compute the next scheduled run timestamp from now (or a given datetime)."""
    from datetime import datetime, timedelta
    base = from_dt or datetime.utcnow()
    if schedule == "weekly":
        return base + timedelta(weeks=1)
    if schedule == "monthly":
        # Advance by ~30 days
        return base + timedelta(days=30)
    return None  # 'manual' — no auto-schedule


def create_tracked_entity(
    entity_name: str, city: str, state: str,
    specialty: str | None, aggregate: bool,
    schedule: str, created_by: str, notes: str = "",
) -> dict:
    import uuid
    from datetime import datetime
    con = get_connection()
    eid = str(uuid.uuid4())
    now = datetime.utcnow()
    next_run = _next_run_at(schedule, now)
    con.execute(
        """INSERT INTO tracked_entities
           (id, entity_name, city, state, specialty, aggregate, schedule,
            last_run_at, next_run_at, created_by, created_at, active, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, TRUE, ?)""",
        [eid, entity_name, city, state, specialty, aggregate, schedule,
         next_run, created_by, now, notes or ""],
    )
    con.close()
    return get_tracked_entity(eid)


def get_tracked_entity(entity_id: str) -> dict | None:
    con = get_connection()
    row = con.execute(
        "SELECT * FROM tracked_entities WHERE id = ?", [entity_id]
    ).fetchone()
    if not row:
        con.close()
        return None
    cols = [d[0] for d in con.description]
    con.close()
    return dict(zip(cols, row))


def list_tracked_entities() -> list[dict]:
    con = get_connection()
    rows = con.execute("""
        SELECT te.*,
               (SELECT COUNT(*) FROM analysis_runs a
                WHERE LOWER(a.entity_name) = LOWER(te.entity_name)
                  AND a.individual_report = TRUE) AS run_count
        FROM tracked_entities te
        ORDER BY te.created_at DESC
    """).fetchall()
    cols = [d[0] for d in con.description]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def update_tracked_entity(entity_id: str, **kwargs) -> None:
    allowed = {"entity_name", "city", "state", "specialty", "aggregate",
               "schedule", "active", "notes", "next_run_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    con = get_connection()
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(
        f"UPDATE tracked_entities SET {sets} WHERE id=?",
        list(fields.values()) + [entity_id],
    )
    con.close()


def mark_tracked_entity_ran(entity_id: str, schedule: str) -> None:
    """Record last_run_at = now and advance next_run_at by the schedule interval."""
    from datetime import datetime
    now = datetime.utcnow()
    next_run = _next_run_at(schedule, now)
    con = get_connection()
    con.execute(
        "UPDATE tracked_entities SET last_run_at=?, next_run_at=? WHERE id=?",
        [now, next_run, entity_id],
    )
    con.close()


def get_due_tracked_entities() -> list[dict]:
    """Return active entities whose next_run_at is in the past (due for a run)."""
    from datetime import datetime
    con = get_connection()
    rows = con.execute(
        """SELECT * FROM tracked_entities
           WHERE active = TRUE
             AND schedule != 'manual'
             AND (next_run_at IS NULL OR next_run_at <= ?)
           ORDER BY next_run_at ASC""",
        [datetime.utcnow()],
    ).fetchall()
    cols = [d[0] for d in con.description]
    con.close()
    return [dict(zip(cols, r)) for r in rows]


def get_entity_trend(entity_name: str) -> list[dict]:
    """Return all historical individual-report snapshots for the named entity,
    oldest first, with AI Visibility Score, tier scores, and Google front-door data."""
    import json
    con = get_connection()
    rows = con.execute(
        """SELECT
               a.run_id,
               a.generated_at,
               a.pdf_path,
               p.ai_visibility_score,
               p.tier_scores,
               p.google_footprint,
               p.leapfrog_grade,
               p.cms_star_rating,
               p.accreditations
           FROM analysis_runs a
           JOIN ranked_providers p ON p.run_id = a.run_id AND p.rank = 1
           WHERE LOWER(a.entity_name) = LOWER(?)
             AND a.individual_report = TRUE
           ORDER BY a.generated_at ASC, a.run_id ASC""",
        [entity_name],
    ).fetchall()
    cols = ["run_id", "generated_at", "pdf_path", "ai_visibility_score",
            "tier_scores", "google_footprint", "leapfrog_grade",
            "cms_star_rating", "accreditations"]
    con.close()

    results = []
    for row in rows:
        d = dict(zip(cols, row))
        ts = json.loads(d.pop("tier_scores") or "{}")
        fp = json.loads(d.pop("google_footprint") or "{}")
        fd = fp.get("front_door", {})
        d["tier_outcomes"]     = ts.get("clinical_outcomes_safety")
        d["tier_credentials"]  = ts.get("credentials_recognition")
        d["tier_experience"]   = ts.get("patient_experience_reviews")
        d["tier_access"]       = ts.get("access_fit")
        d["google_rating"]     = fd.get("rating")
        d["google_count"]      = fd.get("count")
        d["generated_at"]      = str(d["generated_at"])
        results.append(d)
    return results


def get_recent_run(entity_name: str, location: str, days: int = 90) -> dict | None:
    """Return the most recent individual analysis within `days` days for this entity/location.

    Matching is case-insensitive on both entity_name and location. Returns a dict with
    keys run_id, generated_at, result_json, or None if no qualifying run exists.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = get_connection()
    row = con.execute(
        """SELECT run_id, generated_at, result_json
           FROM analysis_runs
           WHERE LOWER(entity_name) = LOWER(?)
             AND LOWER(location) = LOWER(?)
             AND generated_at >= ?
             AND result_json IS NOT NULL
           ORDER BY generated_at DESC, run_id DESC
           LIMIT 1""",
        [entity_name, location, cutoff],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"run_id": str(row[0]), "generated_at": str(row[1]), "result_json": row[2]}
