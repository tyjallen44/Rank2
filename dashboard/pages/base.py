from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from ..config import settings

SCREENSHOTS_DIR = Path("screenshots")


class BasePage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def navigate(self, path: str = "") -> None:
        self.page.goto(f"{settings.dashboard_base_url}/{path.lstrip('/')}")

    def screenshot(self, name: str) -> Path:
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        dest = SCREENSHOTS_DIR / f"{name}.png"
        self.page.screenshot(path=str(dest), full_page=True)
        return dest

    def login(self) -> None:
        self.navigate("login")
        self.page.fill("[name=username], [name=email], #username, #email", settings.dashboard_username)
        self.page.fill("[name=password], #password", settings.dashboard_password)
        self.page.click("[type=submit]")
        self.page.wait_for_load_state("networkidle")
