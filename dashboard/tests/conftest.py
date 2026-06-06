import pytest
from playwright.sync_api import Browser, Page

from dashboard.api import DashboardAPIClient
from dashboard.pages.base import BasePage


@pytest.fixture(scope="session")
def api_client():
    with DashboardAPIClient() as client:
        yield client


@pytest.fixture
def logged_in_page(page: Page) -> Page:
    base = BasePage(page)
    base.login()
    return page
