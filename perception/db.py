from __future__ import annotations

import duckdb
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
    # ── System Composite (Tier 3) tables ─────────────────────────────────────
    # Migration: if network_registries was created with NOT NULL on anchor_run_id, drop and
    # recreate it — the table is always empty at this point (no composite has ever succeeded).
    try:
        info = con.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='network_registries' AND column_name='anchor_run_id'"
        ).fetchone()
        if info and info[0] == 'NO':
            con.execute("DROP TABLE network_registries")
    except Exception:
        pass

    con.execute("""
        CREATE TABLE IF NOT EXISTS network_registries (
            id               VARCHAR PRIMARY KEY,
            anchor_run_id    VARCHAR,
            system_name      VARCHAR NOT NULL,
            market_cbsa      VARCHAR,
            radius_miles     INTEGER DEFAULT 50,
            attested_at      TIMESTAMP,
            re_attest_due    TIMESTAMP,
            created_at       TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS network_entities (
            id                              VARCHAR PRIMARY KEY,
            registry_id                     VARCHAR NOT NULL,
            name                            VARCHAR NOT NULL,
            entity_type                     VARCHAR NOT NULL,
            city                            VARCHAR,
            state                           VARCHAR,
            inclusion_tier                  VARCHAR NOT NULL,
            ownership_evidence_source       VARCHAR DEFAULT '',
            ownership_verified              BOOLEAN DEFAULT FALSE,
            inclusion_weight                DOUBLE DEFAULT 1.0,
            fte_count                       INTEGER,
            encounter_volume_share          DOUBLE,
            strategic_multiplier            DOUBLE DEFAULT 1.0,
            strategic_multiplier_rationale  VARCHAR,
            transition_close_date           DATE,
            linked_run_id                   VARCHAR,
            created_at                      TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS network_battery_runs (
            id                VARCHAR PRIMARY KEY,
            registry_id       VARCHAR NOT NULL,
            composite_run_id  VARCHAR NOT NULL,
            prompt_category   VARCHAR NOT NULL,
            prompt_number     INTEGER NOT NULL,
            prompt_text       VARCHAR NOT NULL,
            assistant         VARCHAR NOT NULL,
            retrieval_mode    VARCHAR NOT NULL,
            response_text     VARCHAR,
            network_resolution VARCHAR NOT NULL,
            run_date          TIMESTAMP,
            created_at        TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS composite_results (
            id                        VARCHAR PRIMARY KEY,
            registry_id               VARCHAR NOT NULL,
            anchor_run_id             VARCHAR NOT NULL,
            hospital_score            DOUBLE,
            network_score             DOUBLE,
            attributed_network_score  DOUBLE,
            sar                       DOUBLE,
            footprint_class           VARCHAR,
            w_h                       DOUBLE,
            w_n                       DOUBLE,
            continuum_coherence       DOUBLE,
            continuum_bonus           DOUBLE,
            composite_score           DOUBLE,
            composite_grade           VARCHAR,
            merged_entity_delta       DOUBLE,
            network_capture_rate      DOUBLE,
            leakage_index             DOUBLE,
            score_ceiling_applied     BOOLEAN DEFAULT FALSE,
            score_ceiling_reason      VARCHAR,
            small_network_refused     BOOLEAN DEFAULT FALSE,
            proxy_weighted            BOOLEAN DEFAULT FALSE,
            modifier_ledger           VARCHAR DEFAULT '[]',
            per_assistant_sar         VARCHAR DEFAULT '{}',
            orphan_entity_ids         VARCHAR DEFAULT '[]',
            rubric_version_hospital   VARCHAR DEFAULT 'hospital-v1.0',
            rubric_version_practice   VARCHAR DEFAULT 'practice-v1.0',
            rubric_version_composite  VARCHAR DEFAULT 'composite-v1.0',
            oldest_input_date         TIMESTAMP,
            composite_expires_at      TIMESTAMP,
            composite_mode            VARCHAR DEFAULT 'hospitals_and_practices',
            created_at                TIMESTAMP
        )
    """)
    # composite_mode on analysis_runs (NULL = plain run, 'hospitals_only', 'hospitals_and_practices')
    existing_run_cols2 = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='analysis_runs'"
    ).fetchall()}
    if "composite_mode" not in existing_run_cols2:
        con.execute("ALTER TABLE analysis_runs ADD COLUMN composite_mode VARCHAR DEFAULT NULL")

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
