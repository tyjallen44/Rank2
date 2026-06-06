from __future__ import annotations

from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from ..models import Review, ReviewSource


def fetch_reviews(entity_id: str, profile_url: str, page: Page) -> Iterator[Review]:
    """Scrape reviews from a ZocDoc provider profile page."""
    page.goto(profile_url, wait_until="networkidle")

    soup = BeautifulSoup(page.content(), "html.parser")
    for i, card in enumerate(soup.select("[class*='ReviewCard']")):
        rating_el = card.select_one("[class*='rating'], [aria-label*='star']")
        text_el = card.select_one("[class*='reviewText'], p")
        date_el = card.select_one("[class*='date'], time")

        yield Review(
            source=ReviewSource.zocdoc,
            entity_id=entity_id,
            review_id=f"zd-{entity_id}-{i}",
            rating=_parse_rating(rating_el) if rating_el else None,
            text=text_el.get_text(strip=True) if text_el else None,
            review_date=_parse_date(date_el.get("datetime") or date_el.get_text()) if date_el else None,
        )


def _parse_rating(el) -> float | None:
    label = el.get("aria-label", "")
    try:
        return float(label.split()[0])
    except (ValueError, IndexError):
        return None


def _parse_date(text: str) -> date | None:
    from dateutil import parser as dateparser
    try:
        return dateparser.parse(text).date()
    except Exception:
        return None
