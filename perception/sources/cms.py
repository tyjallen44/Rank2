from __future__ import annotations

from datetime import date
from typing import Iterator

import httpx
import pandas as pd

from ..models import EntitySummary, ReviewSource

# CMS Care Compare — Hospital General Information
_HOSPITAL_CSV = "https://data.cms.gov/provider-data/sites/default/files/resources/092256becd267d9eecca15f052585779_1657310460/Hospital_General_Information.csv"

# CMS Physician Compare — individual provider ratings
_PHYSICIAN_API = "https://data.cms.gov/provider-data/api/1/datastore/sql"


def fetch_hospital_summaries() -> Iterator[EntitySummary]:
    """Stream CMS overall hospital star ratings."""
    r = httpx.get(_HOSPITAL_CSV, timeout=60)
    r.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(r.text), dtype=str)
    for _, row in df.iterrows():
        rating_raw = row.get("Hospital overall rating", "")
        try:
            rating = float(rating_raw)
        except (ValueError, TypeError):
            rating = None
        provider_id = row.get("Provider ID", "").strip()
        if not provider_id:
            continue
        yield EntitySummary(
            entity_id=f"cms-hosp-{provider_id}",
            source=ReviewSource.cms,
            avg_rating=rating,
            review_count=0,
            as_of=date.today(),
        )


def fetch_provider_summary(npi: str) -> EntitySummary | None:
    """Fetch CMS star rating for a physician by NPI."""
    query = f"[SELECT * FROM c6c37a3e-9387-4b44-b2a4-0a6dc5f26bcb][WHERE npi = '{npi}'][LIMIT 1]"
    r = httpx.get(_PHYSICIAN_API, params={"query": query}, timeout=30)
    r.raise_for_status()
    results = r.json()
    if not results:
        return None
    row = results[0]
    try:
        rating = float(row.get("five_star_overall", 0) or 0)
    except (ValueError, TypeError):
        rating = None
    return EntitySummary(
        entity_id=f"cms-npi-{npi}",
        source=ReviewSource.cms,
        avg_rating=rating,
        review_count=0,
        as_of=date.today(),
    )
