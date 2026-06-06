from __future__ import annotations

import re
import time
from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from ..models import EntitySummary, Review, ReviewSource


def search_and_fetch(entity_id: str, name: str, city: str, state: str, page: Page) -> tuple[EntitySummary, list[Review]]:
    """Navigate Google Maps, find the business, and scrape its rating + reviews."""
    query = f"{name} {city} {state}"
    page.goto(f"https://www.google.com/maps/search/{query.replace(' ', '+')}", wait_until="networkidle")

    # Click the first result if on a results list
    first = page.query_selector("a[href*='/maps/place/']")
    if first:
        first.click()
        page.wait_for_load_state("networkidle")

    summary = _scrape_summary(entity_id, page)
    reviews = list(_scrape_reviews(entity_id, page))
    return summary, reviews


def _scrape_summary(entity_id: str, page: Page) -> EntitySummary:
    soup = BeautifulSoup(page.content(), "html.parser")

    avg_rating: float | None = None
    review_count: int = 0

    # Rating — aria-label like "4.5 stars"
    rating_el = soup.select_one("[aria-label*='stars'], [aria-label*='star']")
    if rating_el:
        m = re.search(r"([\d.]+)", rating_el.get("aria-label", ""))
        if m:
            avg_rating = float(m.group(1))

    # Review count
    count_el = soup.find(string=re.compile(r"[\d,]+ review"))
    if count_el:
        m = re.search(r"([\d,]+)", count_el)
        if m:
            review_count = int(m.group(1).replace(",", ""))

    return EntitySummary(
        entity_id=entity_id,
        source=ReviewSource.google,
        avg_rating=avg_rating,
        review_count=review_count,
        as_of=date.today(),
    )


def _scrape_reviews(entity_id: str, page: Page) -> Iterator[Review]:
    # Click the Reviews tab if present
    reviews_tab = page.query_selector("button[aria-label*='Reviews']")
    if reviews_tab:
        reviews_tab.click()
        page.wait_for_load_state("networkidle")

    # Scroll the review panel to load more reviews
    for _ in range(5):
        page.keyboard.press("End")
        time.sleep(1)

    soup = BeautifulSoup(page.content(), "html.parser")
    for i, card in enumerate(soup.select("[data-review-id], [jslog*='review']")):
        author_el = card.select_one("[class*='d4r55'], .WNxzHc")
        rating_el = card.select_one("[aria-label*='stars'], [aria-label*='star']")
        text_el = card.select_one("[class*='wiI7pd'], .MyEned")
        date_el = card.select_one("[class*='rsqaWe'], .xRkPPb")

        review_id = card.get("data-review-id") or f"gmaps-{entity_id}-{i}"
        rating = None
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get("aria-label", ""))
            if m:
                rating = float(m.group(1))

        yield Review(
            source=ReviewSource.google,
            entity_id=entity_id,
            review_id=str(review_id),
            author=author_el.get_text(strip=True) if author_el else None,
            rating=rating,
            text=text_el.get_text(strip=True) if text_el else None,
            review_date=_parse_date(date_el.get_text(strip=True)) if date_el else None,
        )


def _parse_date(text: str) -> date | None:
    from dateutil import parser as dateparser
    try:
        return dateparser.parse(text, default=None).date()
    except Exception:
        return None
