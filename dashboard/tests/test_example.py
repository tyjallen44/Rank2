"""Placeholder showing the test pattern: browser assertion + API cross-check."""
from playwright.sync_api import Page

from dashboard.api import DashboardAPIClient
from dashboard.pages.base import BasePage


def test_dashboard_loads(logged_in_page: Page):
    page = BasePage(logged_in_page)
    page.navigate("dashboard")
    logged_in_page.wait_for_selector("[data-testid='dashboard-root'], main", timeout=10_000)
    page.screenshot("dashboard_home")


def test_metric_matches_api(logged_in_page: Page, api_client: DashboardAPIClient):
    """Example: value shown on dashboard must match what the API returns."""
    page = BasePage(logged_in_page)
    page.navigate("dashboard")

    # Read the displayed value
    el = logged_in_page.wait_for_selector("[data-testid='total-patients']", timeout=10_000)
    displayed = int(el.inner_text().replace(",", "").strip())

    # Cross-check against API
    api_data = api_client.get("/metrics/patients")
    assert displayed == api_data["total"], (
        f"Dashboard shows {displayed} but API returned {api_data['total']}"
    )
