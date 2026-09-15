#!/usr/bin/env python3
"""One-off: rename 'AI Visibility' -> 'AI Reputation' in the LIVE learn_articles rows.
The Learn + Methodology pages are admin-edited in Postgres, so updating the code seed
alone does not touch rows that already exist. Idempotent.
Usage: .venv/bin/python scripts/rebrand_learn_articles.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from perception.db import init_db, get_connection

PAIRS = [("AI Visibility Intelligence", "AI Reputation Analysis Platform"),
         ("AI Reputation Intelligence", "AI Reputation Analysis Platform"),
         ("AI Visibility", "AI Reputation"), ("AI-Visibility", "AI-Reputation"),
         ("AI visibility", "AI reputation"), ("AI-visibility", "AI-reputation")]


def main() -> None:
    init_db()
    con = get_connection()
    total = 0
    for col in ("title", "body", "category"):
        for old, new in PAIRS:
            cur = con.execute(
                f"UPDATE learn_articles SET {col} = replace({col}, ?, ?) WHERE {col} LIKE ?",
                [old, new, f"%{old}%"])
            total += getattr(cur, "rowcount", 0) or 0
    con.close()
    print(f"learn_articles rows updated: {total}")


if __name__ == "__main__":
    main()
