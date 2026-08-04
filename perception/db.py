from __future__ import annotations

import duckdb
import threading
import time as _time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import settings

# Single shared connection + reentrant lock.
# DuckDB raises "already attached" when multiple threads call duckdb.connect()
# on the same GCS FUSE path concurrently.  One connection serialised behind
# an RLock eliminates that entirely without meaningfully affecting throughput
# (AI API calls dominate runtime, not DB writes).
_db_lock: threading.RLock = threading.RLock()
_db_con: Optional[duckdb.DuckDBPyConnection] = None


def _global_con() -> duckdb.DuckDBPyConnection:
    """Return the single shared DuckDB connection, opening it if needed."""
    global _db_con
    if _db_con is None:
        _db_con = duckdb.connect(str(settings.db_path))
    return _db_con


class _StaleHandleConnection:
    """
    Serialises all DuckDB access behind a module-level RLock and routes every
    operation through a single shared connection.  Transparently retries on
    GCS FUSE stale-handle errors (ESTALE), reopening the shared connection and
    removing the WAL file before each retry.
    """

    def __init__(self) -> None:
        _db_lock.acquire()
        self._locked = True

    def _handle_stale(self, attempt: int) -> None:
        global _db_con
        try:
            if _db_con is not None:
                _db_con.close()
        except Exception:
            pass
        _db_con = None
        wal = Path(str(settings.db_path) + ".wal")
        try:
            if wal.exists():
                wal.unlink()
        except Exception:
            pass
        _time.sleep(0.5 * (attempt + 1))

    @staticmethod
    def _is_stale(exc: Exception) -> bool:
        s = str(exc).lower()
        return "stale file handle" in s or "estale" in s

    def execute(self, query: str, params=None):
        for attempt in range(3):
            try:
                con = _global_con()
                if params is not None:
                    con.execute(query, params)
                else:
                    con.execute(query)
                return self
            except Exception as exc:
                if self._is_stale(exc) and attempt < 2:
                    self._handle_stale(attempt)
                    continue
                raise

    def executemany(self, query: str, params_list=None):
        for attempt in range(3):
            try:
                _global_con().executemany(query, params_list or [])
                return self
            except Exception as exc:
                if self._is_stale(exc) and attempt < 2:
                    self._handle_stale(attempt)
                    continue
                raise

    @property
    def description(self):
        return _global_con().description

    def fetchall(self):
        for attempt in range(3):
            try:
                return _global_con().fetchall()
            except Exception as exc:
                if self._is_stale(exc) and attempt < 2:
                    self._handle_stale(attempt)
                    continue
                raise

    def fetchone(self):
        for attempt in range(3):
            try:
                return _global_con().fetchone()
            except Exception as exc:
                if self._is_stale(exc) and attempt < 2:
                    self._handle_stale(attempt)
                    continue
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        if self._locked:
            try:
                _db_lock.release()
            except Exception:
                pass
            self._locked = False


def get_connection() -> _StaleHandleConnection:
    return _StaleHandleConnection()


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
        ("briefing_pdf_path", "VARCHAR"),
        ("event_id", "VARCHAR"),
        ("created_at", "TIMESTAMP"),
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
        ("number",      "INTEGER"),
        ("notes",       "VARCHAR DEFAULT ''"),
        ("attachments", "VARCHAR DEFAULT '[]'"),
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

    # ── Practice entity registry ──────────────────────────────────────────────
    # Caches the sibling roster discovered for each anchor practice so that
    # back-to-back runs produce an identical entity list.  Keyed on a
    # normalised "{entity_name}|{city}|{state}" anchor string.
    con.execute("""
        CREATE TABLE IF NOT EXISTS practice_entity_registry (
            id            VARCHAR PRIMARY KEY,
            anchor_key    VARCHAR NOT NULL,
            name          VARCHAR NOT NULL,
            entity_type   VARCHAR NOT NULL DEFAULT 'practice',
            city          VARCHAR,
            state         VARCHAR,
            canonical_name VARCHAR,
            registered_at TIMESTAMP NOT NULL,
            expires_at    TIMESTAMP NOT NULL
        )
    """)
    # Index for fast anchor-key lookup
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_per_anchor_key "
            "ON practice_entity_registry (anchor_key)"
        )
    except Exception:
        pass

    # ── GBP durable identity table ───────────────────────────────────────────
    # Keyed by place_id (Google's permanent listing identifier).  Written once
    # on first confirmed strong-match fetch; read at the start of every composite
    # run to seed _assigned_place_ids before any live API call.  This prevents
    # the same listing from sliding between entity names across runs.
    con.execute("""
        CREATE TABLE IF NOT EXISTS gbp_identity (
            place_id      VARCHAR PRIMARY KEY,
            entity_name   VARCHAR NOT NULL,
            city          VARCHAR NOT NULL,
            state         VARCHAR NOT NULL,
            display_name  VARCHAR,
            rating        DOUBLE,
            review_count  INTEGER,
            maps_url      VARCHAR,
            bound_at      TIMESTAMP NOT NULL,
            run_id        VARCHAR
        )
    """)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_gbp_identity_entity "
            "ON gbp_identity (LOWER(entity_name), LOWER(city), LOWER(state))"
        )
    except Exception:
        pass

    # ── Per-row binding audit log ─────────────────────────────────────────────
    # Records which place_id was bound to each entity in each composite run,
    # and whether the binding came from the identity cache or a live fetch.
    con.execute("""
        CREATE TABLE IF NOT EXISTS practice_reputation_run_log (
            id             VARCHAR PRIMARY KEY,
            run_id         VARCHAR NOT NULL,
            entity_name    VARCHAR NOT NULL,
            place_id       VARCHAR,
            rating         DOUBLE,
            review_count   INTEGER,
            binding_source VARCHAR,
            binding_reason VARCHAR,
            logged_at      TIMESTAMP
        )
    """)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_prrl_run_id "
            "ON practice_reputation_run_log (run_id)"
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

    # ── FQHC Community Health Edition tables ─────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS fqhc_intake (
            id           VARCHAR PRIMARY KEY,
            run_id       VARCHAR NOT NULL UNIQUE,
            entity_name  VARCHAR NOT NULL,
            city         VARCHAR,
            state        VARCHAR,
            intake_json  VARCHAR NOT NULL,
            created_at   TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS fqhc_fact_audit (
            id                VARCHAR PRIMARY KEY,
            run_id            VARCHAR NOT NULL,
            row_order         INTEGER,
            claim             VARCHAR,
            ai_representation VARCHAR,
            flag              VARCHAR,
            severity          VARCHAR,
            points            DOUBLE,
            created_at        TIMESTAMP
        )
    """)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_fqhc_fact_run "
            "ON fqhc_fact_audit (run_id)"
        )
    except Exception:
        pass
    con.execute("""
        CREATE TABLE IF NOT EXISTS fqhc_battery_runs (
            id            VARCHAR PRIMARY KEY,
            fqhc_run_id   VARCHAR NOT NULL,
            query         TEXT,
            language      VARCHAR,
            category      VARCHAR,
            assistant     VARCHAR,
            response_text TEXT,
            surfaced      BOOLEAN,
            created_at    TIMESTAMP
        )
    """)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_battery_fqhc_run "
            "ON fqhc_battery_runs (fqhc_run_id)"
        )
    except Exception:
        pass
    # Add edition and mqcr columns to analysis_runs
    _ar_cols2 = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='analysis_runs'"
    ).fetchall()}
    if "edition" not in _ar_cols2:
        con.execute("ALTER TABLE analysis_runs ADD COLUMN edition VARCHAR")
    if "mqcr" not in _ar_cols2:
        con.execute("ALTER TABLE analysis_runs ADD COLUMN mqcr DOUBLE")

    # ── Events Pulse tables ───────────────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS event_runs (
            id                VARCHAR PRIMARY KEY,
            event_name        VARCHAR NOT NULL,
            event_date        VARCHAR,
            entity_type       VARCHAR NOT NULL,
            csv_filename      VARCHAR,
            total_count       INTEGER DEFAULT 0,
            done_count        INTEGER DEFAULT 0,
            skip_count        INTEGER DEFAULT 0,
            status            VARCHAR DEFAULT 'running',
            user_role         VARCHAR DEFAULT 'user',
            enriched_csv_path VARCHAR,
            zip_path          VARCHAR,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate existing event_runs rows that pre-date zip_path
    _er_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='event_runs'"
    ).fetchall()}
    if "zip_path" not in _er_cols:
        con.execute("ALTER TABLE event_runs ADD COLUMN zip_path VARCHAR")
    # ── Network Pulse tables ──────────────────────────────────────────────────
    init_network_db(con)

    con.execute("""
        CREATE TABLE IF NOT EXISTS event_entities (
            id             VARCHAR PRIMARY KEY,
            event_id       VARCHAR NOT NULL,
            row_num        INTEGER NOT NULL,
            input_name     VARCHAR,
            input_city     VARCHAR,
            input_state    VARCHAR,
            input_url      VARCHAR,
            input_customer VARCHAR,
            resolved_name  VARCHAR,
            resolved_addr  VARCHAR,
            run_id         VARCHAR,
            pulse_score    INTEGER,
            letter_grade   VARCHAR,
            band_label     VARCHAR,
            status         VARCHAR DEFAULT 'pending',
            error_msg      VARCHAR
        )
    """)
    # Migrate existing event_entities rows that pre-date these columns
    _ee_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='event_entities'"
    ).fetchall()}
    for _col in ("input_url", "input_customer"):
        if _col not in _ee_cols:
            con.execute(f"ALTER TABLE event_entities ADD COLUMN {_col} VARCHAR")
    con.close()


# ── Network Pulse helpers ─────────────────────────────────────────────────────

def init_network_db(con=None) -> None:
    """Create the network_runs table if it does not already exist.

    Can be called with an existing connection (during init_db) or standalone.
    """
    _own_con = con is None
    if _own_con:
        con = get_connection()

    con.execute("""
        CREATE TABLE IF NOT EXISTS network_runs (
            run_id              VARCHAR PRIMARY KEY,
            network_name        VARCHAR NOT NULL,
            hq_location         VARCHAR,
            source_url          VARCHAR,
            facility_type       VARCHAR DEFAULT 'hospital',
            total_hospitals     INTEGER,
            ai_visibility_score INTEGER,
            grade               VARCHAR,
            generated_at        DATE NOT NULL,
            result_json         VARCHAR,
            pdf_path            VARCHAR,
            teaser_pdf_path     VARCHAR,
            user_role           VARCHAR DEFAULT 'admin'
        )
    """)

    # Migrate older DBs that may be missing columns
    _nr_cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='network_runs'"
    ).fetchall()}
    for _col, _def in [
        ("hq_location",         "VARCHAR"),
        ("source_url",          "VARCHAR"),
        ("facility_type",       "VARCHAR DEFAULT 'hospital'"),
        ("total_hospitals",     "INTEGER"),
        ("ai_visibility_score", "INTEGER"),
        ("grade",               "VARCHAR"),
        ("result_json",         "VARCHAR"),
        ("pdf_path",            "VARCHAR"),
        ("teaser_pdf_path",     "VARCHAR"),
        ("user_role",           "VARCHAR DEFAULT 'admin'"),
        ("created_at",          "TIMESTAMP"),
    ]:
        if _col not in _nr_cols:
            try:
                con.execute(f"ALTER TABLE network_runs ADD COLUMN {_col} {_def}")
            except Exception:
                pass

    if _own_con:
        con.close()


# ── Event Pulse helpers ───────────────────────────────────────────────────────

def create_event_run(
    event_id: str, event_name: str, event_date: Optional[str],
    entity_type: str, csv_filename: Optional[str],
    total_count: int, role: str,
) -> None:
    from datetime import datetime
    con = get_connection()
    con.execute(
        "INSERT INTO event_runs (id, event_name, event_date, entity_type, csv_filename, "
        "total_count, done_count, skip_count, status, user_role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'running', ?, ?)",
        [event_id, event_name, event_date, entity_type, csv_filename, total_count, role,
         datetime.utcnow()],
    )
    con.close()


def create_event_entities(entities: list) -> None:
    con = get_connection()
    for e in entities:
        con.execute(
            "INSERT INTO event_entities (id, event_id, row_num, input_name, input_city, "
            "input_state, input_url, input_customer, resolved_name, resolved_addr, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            [e["id"], e["event_id"], e["row_num"], e["input_name"], e["input_city"],
             e["input_state"], e.get("input_url", ""), e.get("input_customer", ""),
             e["resolved_name"], e["resolved_addr"]],
        )
    con.close()


def update_event_entity(
    entity_id: str, run_id: Optional[str], pulse_score: Optional[int],
    letter_grade: str, band_label: str, status: str,
    error_msg: Optional[str] = None,
) -> None:
    con = get_connection()
    con.execute(
        "UPDATE event_entities SET run_id=?, pulse_score=?, letter_grade=?, band_label=?, "
        "status=?, error_msg=? WHERE id=?",
        [run_id, pulse_score, letter_grade, band_label, status, error_msg, entity_id],
    )
    con.close()


def increment_event_progress(event_id: str, done: int = 0, skipped: int = 0) -> None:
    con = get_connection()
    con.execute(
        "UPDATE event_runs SET done_count = done_count + ?, skip_count = skip_count + ? WHERE id = ?",
        [done, skipped, event_id],
    )
    con.close()


def update_event_files(event_id: str, enriched_csv_path: Optional[str] = None, zip_path: Optional[str] = None) -> None:
    """Update only the file paths for an event run without changing status."""
    con = get_connection()
    if enriched_csv_path is not None and zip_path is not None:
        con.execute(
            "UPDATE event_runs SET enriched_csv_path=?, zip_path=? WHERE id=?",
            [enriched_csv_path, zip_path, event_id],
        )
    elif enriched_csv_path is not None:
        con.execute(
            "UPDATE event_runs SET enriched_csv_path=? WHERE id=?",
            [enriched_csv_path, event_id],
        )
    elif zip_path is not None:
        con.execute(
            "UPDATE event_runs SET zip_path=? WHERE id=?",
            [zip_path, event_id],
        )
    con.close()


def finalize_event_run(event_id: str, enriched_csv_path: str, zip_path: str = "") -> None:
    con = get_connection()
    con.execute(
        "UPDATE event_runs SET status='complete', enriched_csv_path=?, zip_path=? WHERE id=?",
        [enriched_csv_path, zip_path or None, event_id],
    )
    con.close()


def update_event_run_meta(event_id: str, event_name: Optional[str] = None, event_date: Optional[str] = None) -> None:
    fields, values = [], []
    if event_name is not None:
        fields.append("event_name = ?"); values.append(event_name)
    if event_date is not None:
        fields.append("event_date = ?"); values.append(event_date or None)
    if not fields:
        return
    con = get_connection()
    con.execute(f"UPDATE event_runs SET {', '.join(fields)} WHERE id = ?", values + [event_id])
    con.close()


def get_event_run(event_id: str) -> Optional[dict]:
    con = get_connection()
    row = con.execute(
        "SELECT id, event_name, event_date, entity_type, csv_filename, total_count, "
        "done_count, skip_count, status, user_role, enriched_csv_path, zip_path, created_at "
        "FROM event_runs WHERE id = ?",
        [event_id],
    ).fetchone()
    con.close()
    if not row:
        return None
    cols = ["id", "event_name", "event_date", "entity_type", "csv_filename",
            "total_count", "done_count", "skip_count", "status", "user_role",
            "enriched_csv_path", "zip_path", "created_at"]
    return dict(zip(cols, row))


def get_event_entities(event_id: str) -> list:
    con = get_connection()
    rows = con.execute(
        "SELECT id, event_id, row_num, input_name, input_city, input_state, input_url, "
        "input_customer, resolved_name, resolved_addr, run_id, pulse_score, letter_grade, "
        "band_label, status, error_msg FROM event_entities WHERE event_id = ? ORDER BY row_num",
        [event_id],
    ).fetchall()
    con.close()
    cols = ["id", "event_id", "row_num", "input_name", "input_city", "input_state", "input_url",
            "input_customer", "resolved_name", "resolved_addr", "run_id", "pulse_score",
            "letter_grade", "band_label", "status", "error_msg"]
    return [dict(zip(cols, r)) for r in rows]


def list_event_runs(role: str) -> list:
    """Return all event runs — visible to every logged-in user regardless of role."""
    con = get_connection()
    rows = con.execute(
        "SELECT id, event_name, event_date, entity_type, total_count, done_count, "
        "skip_count, status, enriched_csv_path, zip_path, created_at "
        "FROM event_runs ORDER BY created_at DESC"
    ).fetchall()
    con.close()
    cols = ["id", "event_name", "event_date", "entity_type", "total_count",
            "done_count", "skip_count", "status", "enriched_csv_path", "zip_path", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def delete_event_run(event_id: str) -> None:
    """Delete an event run and all associated analysis runs + provider data."""
    con = get_connection()
    # Collect run_ids belonging to this event so we can cascade to ranked_providers
    run_ids = [r[0] for r in con.execute(
        "SELECT run_id FROM event_entities WHERE event_id = ? AND run_id IS NOT NULL",
        [event_id],
    ).fetchall()]
    if run_ids:
        placeholders = ",".join("?" * len(run_ids))
        con.execute(f"DELETE FROM ranked_providers WHERE run_id IN ({placeholders})", run_ids)
        con.execute(f"DELETE FROM analysis_runs WHERE run_id IN ({placeholders})", run_ids)
    con.execute("DELETE FROM event_entities WHERE event_id = ?", [event_id])
    con.execute("DELETE FROM event_runs WHERE id = ?", [event_id])
    con.close()


def set_run_role(run_id: str, role: str) -> None:
    """Tag an analysis run with the role of the user who created it."""
    con = get_connection()
    con.execute("UPDATE analysis_runs SET user_role = ? WHERE run_id = ?", [role, run_id])
    con.close()


def query_history(role: str) -> list[dict[str, Any]]:
    """Return analysis runs + network runs for the given role, newest first."""
    con = get_connection()

    analysis_cols = ["run_id", "location", "specialty", "generated_at",
                     "pdf_path", "md_path", "briefing_pdf_path", "event_id",
                     "entity_type", "mqcr", "provider_count", "created_at"]

    if role == "admin":
        analysis_rows = con.execute("""
            SELECT
                a.run_id,
                a.location,
                a.specialty,
                a.generated_at,
                a.pdf_path,
                a.md_path,
                a.briefing_pdf_path,
                a.event_id,
                a.entity_type,
                a.mqcr,
                COUNT(p.rank) AS provider_count,
                a.created_at
            FROM analysis_runs a
            LEFT JOIN ranked_providers p ON p.run_id = a.run_id
            GROUP BY a.run_id, a.location, a.specialty, a.generated_at,
                     a.pdf_path, a.md_path, a.briefing_pdf_path, a.event_id,
                     a.entity_type, a.mqcr, a.created_at
            ORDER BY a.generated_at DESC, a.run_id DESC
        """).fetchall()
        network_rows = con.execute("""
            SELECT run_id, network_name, COALESCE(facility_type, 'hospital'),
                   generated_at, pdf_path, total_hospitals, created_at
            FROM network_runs
            ORDER BY generated_at DESC, run_id DESC
        """).fetchall()
    else:
        analysis_rows = con.execute("""
            SELECT
                a.run_id,
                a.location,
                a.specialty,
                a.generated_at,
                a.pdf_path,
                a.md_path,
                a.briefing_pdf_path,
                a.event_id,
                a.entity_type,
                a.mqcr,
                COUNT(p.rank) AS provider_count,
                a.created_at
            FROM analysis_runs a
            LEFT JOIN ranked_providers p ON p.run_id = a.run_id
            WHERE a.user_role = ?
            GROUP BY a.run_id, a.location, a.specialty, a.generated_at,
                     a.pdf_path, a.md_path, a.briefing_pdf_path, a.event_id,
                     a.entity_type, a.mqcr, a.created_at
            ORDER BY a.generated_at DESC, a.run_id DESC
        """, [role]).fetchall()
        network_rows = con.execute("""
            SELECT run_id, network_name, COALESCE(facility_type, 'hospital'),
                   generated_at, pdf_path, total_hospitals, created_at
            FROM network_runs
            WHERE COALESCE(user_role, 'admin') = ?
            ORDER BY generated_at DESC, run_id DESC
        """, [role]).fetchall()

    con.close()

    results = [dict(zip(analysis_cols, row)) for row in analysis_rows]

    for row in network_rows:
        run_id, network_name, facility_type, generated_at, pdf_path, total, created_at = row
        results.append({
            "run_id":            run_id,
            "location":          network_name,
            "specialty":         facility_type,
            "generated_at":      generated_at,
            "pdf_path":          pdf_path,
            "md_path":           None,
            "briefing_pdf_path": None,
            "event_id":          None,
            "entity_type":       "hospital_network",
            "mqcr":              None,
            "provider_count":    total or 0,
            "report_type":       "network",
            "created_at":        created_at,
        })

    results.sort(key=lambda r: (str(r.get("generated_at") or ""), r["run_id"]), reverse=True)
    return results


def update_briefing_pdf_path(run_id: str, path: str) -> None:
    """Persist briefing_pdf_path after it has been generated (post-_save_to_db)."""
    con = get_connection()
    con.execute(
        "UPDATE analysis_runs SET briefing_pdf_path = ? WHERE run_id = ?",
        [path, run_id],
    )
    con.close()


def create_feedback(title: str, ftype: str, body: str, submitted_by: str,
                    attachments: Optional[list] = None) -> dict:
    import uuid, json
    from datetime import datetime
    con = get_connection()
    fid = str(uuid.uuid4())
    now = datetime.utcnow()
    att = json.dumps(attachments or [])
    row = con.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM feedback").fetchone()
    next_num = row[0] if row else 1
    con.execute(
        "INSERT INTO feedback (id, number, title, type, body, submitted_by, submitted_at, action, notes, attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '', ?)",
        [fid, next_num, title, ftype, body, submitted_by, now, att]
    )
    con.close()
    return {"id": fid, "number": next_num, "title": title, "type": ftype, "body": body,
            "submitted_by": submitted_by, "submitted_at": str(now), "action": "pending",
            "notes": "", "attachments": attachments or []}


def list_feedback() -> list[dict]:
    import json
    con = get_connection()
    rows = con.execute("""
        SELECT id, number, title, type, body, submitted_by, submitted_at, action, notes, attachments
        FROM feedback
        ORDER BY submitted_at DESC
    """).fetchall()
    con.close()
    cols = ["id", "number", "title", "type", "body", "submitted_by", "submitted_at", "action", "notes", "attachments"]
    result = []
    for r in rows:
        d = dict(zip(cols, r))
        d["submitted_at"] = str(r[6])
        try:
            d["attachments"] = json.loads(r[9] or "[]")
        except Exception:
            d["attachments"] = []
        result.append(d)
    return result


def update_feedback(feedback_id: str, **kwargs) -> None:
    allowed = {"title", "type", "body", "action", "notes", "attachments"}
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


def get_recent_run(
    entity_name: str,
    location: str,
    days: int = 90,
    entity_type: str | None = None,
    aggregate: bool | None = None,
) -> dict | None:
    """Return the most recent individual analysis within `days` days for this entity/location.

    Matching is case-insensitive on both entity_name and location.  Pass
    entity_type="practice" or entity_type="hospital" to restrict the cache
    lookup to the correct report path — this prevents a cached hospital result
    from being served to a practice request (and vice-versa).  Pass aggregate=True/False
    to differentiate aggregate vs. non-aggregate practice runs.

    Returns a dict with keys run_id, generated_at, result_json, or None.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = get_connection()

    if entity_type == "practice":
        type_clause = "AND entity_type = 'practice'"
        params = [entity_name, location, cutoff]
    elif entity_type == "hospital":
        type_clause = "AND (entity_type = 'hospital' OR entity_type IS NULL)"
        params = [entity_name, location, cutoff]
    elif entity_type == "community_health":
        type_clause = "AND entity_type = 'community_health'"
        params = [entity_name, location, cutoff]
    else:
        type_clause = ""
        params = [entity_name, location, cutoff]

    agg_clause = ""
    if aggregate is not None:
        agg_clause = "AND aggregate = ?"
        params = list(params) + [aggregate]

    row = con.execute(
        f"""SELECT run_id, generated_at, result_json
           FROM analysis_runs
           WHERE LOWER(entity_name) = LOWER(?)
             AND LOWER(location) = LOWER(?)
             AND generated_at >= ?
             AND result_json IS NOT NULL
             {type_clause}
             {agg_clause}
           ORDER BY generated_at DESC, run_id DESC
           LIMIT 1""",
        params,
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"run_id": str(row[0]), "generated_at": str(row[1]), "result_json": row[2]}


def get_recent_network_run(network_name: str, days: int = 0) -> dict | None:
    """Return the most recent Network Pulse run for this network within `days` days.

    Pass days=0 for same-day cache (today only).
    Returns dict with keys run_id, generated_at, result_json, or None.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_connection() as con:
        row = con.execute(
            """SELECT run_id, generated_at, result_json
               FROM network_runs
               WHERE LOWER(network_name) = LOWER(?)
                 AND generated_at >= ?
                 AND result_json IS NOT NULL
               ORDER BY generated_at DESC, run_id DESC
               LIMIT 1""",
            [network_name, cutoff],
        ).fetchone()
    if not row:
        return None
    return {"run_id": str(row[0]), "generated_at": str(row[1]), "result_json": row[2]}


def get_gbp_identity(
    entity_name: str, city: str, state: str, days: int = 90
) -> dict | None:
    """Return a durable GBP binding for this entity if one exists within TTL.

    Returns dict with keys: place_id, rating, review_count, maps_url, display_name.
    """
    from datetime import datetime, timedelta as _td
    cutoff = (datetime.utcnow() - _td(days=days)).isoformat()
    con = get_connection()
    row = con.execute(
        """SELECT place_id, rating, review_count, maps_url, display_name
           FROM gbp_identity
           WHERE LOWER(entity_name) = LOWER(?)
             AND LOWER(city) = LOWER(?)
             AND LOWER(state) = LOWER(?)
             AND bound_at >= ?""",
        [entity_name, city, state, cutoff],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {
        "place_id": row[0], "rating": row[1],
        "review_count": row[2], "maps_url": row[3], "display_name": row[4],
    }


def set_gbp_identity(
    place_id: str,
    entity_name: str,
    city: str,
    state: str,
    display_name: str | None,
    rating: float | None,
    review_count: int | None,
    maps_url: str | None,
    run_id: str,
) -> None:
    """Write or update the durable GBP binding for a place_id.

    Uses INSERT OR REPLACE so that a stronger/fresher binding supersedes a
    stale one without leaving orphan rows.
    """
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    con = get_connection()
    con.execute(
        """INSERT OR REPLACE INTO gbp_identity
           (place_id, entity_name, city, state, display_name,
            rating, review_count, maps_url, bound_at, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [place_id, entity_name, city, state, display_name,
         rating, review_count, maps_url, now, run_id],
    )
    con.close()


def log_gbp_binding(
    run_id: str,
    entity_name: str,
    place_id: str | None,
    rating: float | None,
    review_count: int | None,
    binding_source: str,
    binding_reason: str,
) -> None:
    """Append one row to the per-run GBP binding audit log."""
    import uuid as _uuid
    from datetime import datetime
    con = get_connection()
    con.execute(
        """INSERT INTO practice_reputation_run_log
           (id, run_id, entity_name, place_id, rating, review_count,
            binding_source, binding_reason, logged_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [str(_uuid.uuid4()), run_id, entity_name, place_id,
         rating, review_count, binding_source, binding_reason,
         datetime.utcnow().isoformat()],
    )
    con.close()
