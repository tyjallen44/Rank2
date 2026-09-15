#!/usr/bin/env python3
"""Sync the LIVE learn_articles rows (Learn + Methodology pages) to the code seed.

The seed in perception/learn_seed.py is the reviewed source of truth for the
starter posts. This script matches each seed article to a live row by page +
title (with a rename map for retitled posts) and updates title, category and
body. Custom articles that are not in the seed are left alone.

Default is a DRY RUN that prints what would change. Pass --apply to write.
Usage: .venv/bin/python scripts/apply_learn_content.py [--apply]
"""
import sys, pathlib, difflib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from perception.db import init_db, list_learn_articles, update_learn_article
from perception.learn_seed import STARTER_ARTICLES, METHODOLOGY_ARTICLES

# old live title → new seed title (only where a post was renamed)
RENAMES = {
    "learn": {"Why AI visibility matters": "Why AI reputation matters"},
    "methodology": {"How AI assistants are queried": "How the score is produced"},
}

def main(apply: bool) -> None:
    init_db()
    changed = 0
    for page, seed in (("learn", STARTER_ARTICLES), ("methodology", METHODOLOGY_ARTICLES)):
        live = list_learn_articles(include_unpublished=True, page=page)
        by_title = {a["title"].strip().lower(): a for a in live}
        for art in seed:
            new_title = art["title"]
            old_title = next((o for o, n in RENAMES[page].items() if n == new_title), new_title)
            row = by_title.get(new_title.strip().lower()) or by_title.get(old_title.strip().lower())
            if not row:
                print(f"[{page}] MISSING live row for '{new_title}' — use 'Load starter articles' to add it")
                continue
            same = (row["title"] == new_title and (row.get("category") or "") == art["category"]
                    and (row.get("body") or "").strip() == art["body"].strip())
            if same:
                continue
            changed += 1
            print(f"[{page}] UPDATE '{row['title']}'" + (f" → '{new_title}'" if row["title"] != new_title else ""))
            if not apply:
                for line in difflib.unified_diff((row.get("body") or "").splitlines(), art["body"].splitlines(),
                                                 lineterm="", n=0):
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                        print("    " + line[:140])
            else:
                update_learn_article(row["id"], title=new_title, category=art["category"], body=art["body"])
    print(f"\n{'Applied' if apply else 'Would update'}: {changed} article(s)." +
          ("" if apply else "  Re-run with --apply to write."))

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
