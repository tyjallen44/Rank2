from __future__ import annotations

import duckdb
from pathlib import Path

from .config import settings


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
    con.close()
