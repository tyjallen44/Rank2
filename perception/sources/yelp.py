from __future__ import annotations

import re
import time
from datetime import date
from typing import Iterator

from bs4 import BeautifulSoup
from playwright.sync_api import Page

from ..models import EntitySummary, Review, ReviewSource


def search_and_fetch(entity_id: str, name: str, city: str, state: str, page: Page) -> tuple[EntitySummary, list[Review]]:
    """Search Yelp for the business and scrape its rating + reviews."""
    query = name.replace(" ", "+")
    location = f"{city}+{state}".replace(" ", "+")
    page.goto(
        f"https://www.yelp.com/search?find_desc={query}&find_loc={location}&cflt=health",
        wait_until="networkidle",
    )

    # Click the first result
    first = page.query_selector("a[href*='/biz/']")
    if not first:
        summary = EntitySummary(entity_id=entity_id, source=ReviewSource.yelp, review_count=0, as_of=date.today())
        return summary, []

    first.click()
    page.wait_for_load_state("networkidle")

    summary = _scrape_summary(entity_id, page)
    reviews = list(_scrape_reviews(entity_id, page))
    return summary, reviews


def _scrape_summary(entity_id: str, page: Page) -> EntitySummary:
    soup = BeautifulSoup(page.content(), "html.parser")

    avg_rating: float | None = None
    review_count: int = 0

    rating_el = soup.select_one("[aria-label*=' star rating']")
    if rating_el:
        m = re.search(r"([\d.]+)", rating_el.get("aria-label", ""))
        if m:
            avg_rating = float(m.group(1))

    count_el = soup.find(string=re.compile(r"[\d,]+ review"))
    if count_el:
        m = re.search(r"([\d,]+)", count_el)
        if m:
            review_count = int(m.group(1).replace(",", ""))

    return EntitySummary(
        entity_id=entity_id,
        source=ReviewSource.yelp,
        avg_rating=avg_rating,
        review_count=review_count,
        as_of=date.today(),
    )


def _scrape_reviews(entity_id: str, page: Page) -> Iterator[Review]:
    for _ in range(3):
        page.keyboard.press("End")
        time.sleep(1)

    soup = BeautifulSoup(page.content(), "html.parser")
    for i, card in enumerate(soup.select("[class*='reviewItem__'], li[class*='arrange-unit']")):
        author_el = card.select_one("[class*='user-name'], .css-1m051bw")
        rating_el = card.select_one("[aria-label*=' star rating']")
        text_el = card.select_one("[class*='comment__'], p[lang]")
        date_el = card.select_one("[class*='reviewDate'], span[class*='css-chan6m']")

        rating = None
        if rating_el:
            m = re.search(r"([\d.]+)", rating_el.get("aria-label", ""))
            if m:
                rating = float(m.group(1))

        yield Review(
            source=ReviewSource.yelp,
            entity_id=entity_id,
            review_id=f"yelp-{entity_id}-{i}",
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
