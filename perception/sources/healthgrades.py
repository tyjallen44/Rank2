from __future__ import annotations

from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from ..models import Review, ReviewSource


def fetch_reviews(entity_id: str, profile_url: str, page: Page) -> Iterator[Review]:
    """Scrape reviews from a Healthgrades provider profile page."""
    page.goto(profile_url, wait_until="networkidle")

    while True:
        soup = BeautifulSoup(page.content(), "html.parser")
        for card in soup.select("[data-qa='review-card']"):
            rating_el = card.select_one("[data-qa='star-rating']")
            text_el = card.select_one("[data-qa='review-text']")
            date_el = card.select_one("[data-qa='review-date']")
            review_id_el = card.get("data-review-id") or card.get("id", "")

            yield Review(
                source=ReviewSource.healthgrades,
                entity_id=entity_id,
                review_id=f"hg-{entity_id}-{review_id_el}",
                rating=float(rating_el["aria-label"].split()[0]) if rating_el and rating_el.get("aria-label") else None,
                text=text_el.get_text(strip=True) if text_el else None,
                review_date=_parse_date(date_el.get_text(strip=True)) if date_el else None,
            )

        next_btn = page.query_selector("[data-qa='pagination-next']:not([disabled])")
        if not next_btn:
            break
        next_btn.click()
        page.wait_for_load_state("networkidle")


def _parse_date(text: str) -> date | None:
    from dateutil import parser as dateparser
    try:
        return dateparser.parse(text).date()
    except Exception:
        return None
