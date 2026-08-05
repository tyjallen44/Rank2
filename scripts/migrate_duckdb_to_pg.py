#!/usr/bin/env python3
"""One-time copy of Rank2 data from the legacy DuckDB file into Postgres.

Uses DuckDB's native Postgres scanner (`ATTACH ... TYPE postgres`) so the whole
copy runs inside one DuckDB connection — no hand-rolled row streaming.

Design notes
------------
* The Postgres schema must already exist. Create it first by running
  `perception.db.init_db()` against the target DATABASE_URL (idempotent).
* Copy is **column-name driven**, not `SELECT *`: for each table we take the
  columns Postgres actually has (the fresh schema) intersected with the columns
  present in the DuckDB file, in Postgres order. This survives any historical
  column-order drift in the old 1GB file.
* Only tables that exist in BOTH databases are copied.
* Re-runnable with --truncate (clears each target table first). Without it,
  it assumes a fresh/empty target and does plain INSERTs.

Usage
-----
    # 1) create schema in the target first
    DATABASE_URL=postgresql://... python -c "from perception.db import init_db; init_db()"

    # 2) copy the data
    python scripts/migrate_duckdb_to_pg.py \
        --duckdb ./rank2.duckdb \
        --pg "$DATABASE_URL" \
        [--truncate]

Do this when no report run is in flight (see the migration plan in memory).
Work off a *copy* of the live file for safety.
"""
from __future__ import annotations

import argparse
import os
import sys

import duckdb
import psycopg


def _duck_tables(duck: duckdb.DuckDBPyConnection) -> set[str]:
    rows = duck.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_catalog = 'duck' AND table_schema = 'main'"
    ).fetchall()
    return {r[0] for r in rows}


def _duck_columns(duck: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = duck.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_catalog = 'duck' AND table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchall()
    return {r[0] for r in rows}


def _pg_tables_and_columns(pg_dsn: str) -> dict[str, list[str]]:
    """Return {table_name: [columns in ordinal order]} for public schema."""
    out: dict[str, list[str]] = {}
    with psycopg.connect(pg_dsn) as con:
        cur = con.cursor()
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "ORDER BY table_name, ordinal_position"
        )
        for table, col in cur.fetchall():
            out.setdefault(table, []).append(col)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duckdb", default="rank2.duckdb", help="Path to the DuckDB file")
    ap.add_argument("--pg", default=os.environ.get("DATABASE_URL", ""),
                    help="Postgres DSN (defaults to $DATABASE_URL)")
    ap.add_argument("--truncate", action="store_true",
                    help="TRUNCATE each target table before copying (re-runnable)")
    args = ap.parse_args()

    if not args.pg:
        print("ERROR: no Postgres DSN (pass --pg or set DATABASE_URL)", file=sys.stderr)
        return 2
    if not os.path.exists(args.duckdb):
        print(f"ERROR: DuckDB file not found: {args.duckdb}", file=sys.stderr)
        return 2

    # In-memory root so we can attach the DuckDB file read-only while keeping
    # the Postgres target writable (a read-only root would make pg read-only too).
    duck = duckdb.connect()
    duck.execute("INSTALL postgres; LOAD postgres;")
    duck.execute(f"ATTACH '{args.duckdb}' AS duck (READ_ONLY)")
    duck.execute(f"ATTACH '{args.pg}' AS pg (TYPE postgres)")

    duck_tables = _duck_tables(duck)
    pg_schema = _pg_tables_and_columns(args.pg)

    tables = [t for t in pg_schema if t in duck_tables]
    skipped = sorted(set(pg_schema) - set(tables))
    if skipped:
        print(f"(tables in PG but not in DuckDB, skipped): {', '.join(skipped)}")

    total = 0
    for table in sorted(tables):
        dcols = _duck_columns(duck, table)
        cols = [c for c in pg_schema[table] if c in dcols]  # PG order, common only
        if not cols:
            print(f"  {table}: no common columns, skipped")
            continue
        col_list = ", ".join(f'"{c}"' for c in cols)

        if args.truncate:
            duck.execute(f'DELETE FROM pg.{table}')  # clear target (no FKs to worry about)

        src_count = duck.execute(f'SELECT COUNT(*) FROM duck.{table}').fetchone()[0]
        duck.execute(
            f'INSERT INTO pg.{table} ({col_list}) SELECT {col_list} FROM duck.{table}'
        )
        dst_count = duck.execute(f'SELECT COUNT(*) FROM pg.{table}').fetchone()[0]
        total += src_count
        flag = "OK" if dst_count >= src_count else "!! MISMATCH"
        print(f"  {table}: copied {src_count} rows  (pg now has {dst_count})  {flag}")

    print(f"\nDone. {total} source rows copied across {len(tables)} tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
