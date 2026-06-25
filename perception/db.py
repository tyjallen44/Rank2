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
        # AI Visibility Score additions
        ("weighting_profile", "VARCHAR"),
        ("market_overview", "VARCHAR"),
        ("ai_visibility_verdict", "VARCHAR"),
        ("coverage_note", "VARCHAR"),
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
    ]:
        if col not in existing_provider_cols:
            con.execute(f"ALTER TABLE ranked_providers ADD COLUMN {col} {definition}")
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
