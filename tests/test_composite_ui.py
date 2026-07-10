"""UI tests for the System Composite checkbox behavior.

Tests:
  - Composite checkboxes hidden for specialty type
  - Mutual exclusivity between agg and composite checkboxes
  - Defaults: agg=checked, composite=unchecked
  - Network assembly step shown/hidden correctly
  - Add Entity modal toggled by link click

Run with: python -m pytest tests/test_composite_ui.py -v
Requires: playwright installed and browsers downloaded.
"""
import re
import os
import sys
import time
import threading
import pytest

# Skip entire module if playwright not available
pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright, expect

# ── Minimal dev server fixture ────────────────────────────────────────────────

HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "index.html",
)


@pytest.fixture(scope="module")
def base_url():
    """Serve the static index.html on a random port via Python's HTTP server."""
    import http.server
    import socketserver
    web_dir = os.path.dirname(HTML_PATH)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=web_dir, **kw)
        def log_message(self, *_):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{port}/index.html"
        srv.shutdown()


@pytest.fixture(scope="module")
def browser_ctx(base_url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx     = browser.new_context()
        yield ctx, base_url
        ctx.close()
        browser.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def navigate_to_individual(page, base_url):
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    # Click the Individual Report nav link
    page.click("[onclick=\"showPage('individual')\"]")


def advance_to_step3(page):
    """Simulate selecting a hospital entity to reach ir-step3."""
    # Inject a pre-selected entity into JS state and reveal step3 manually
    page.evaluate("""() => {
        window._irSelected = { name: 'TEST HOSPITAL' };
        window._irResolvedCity  = 'Birmingham';
        window._irResolvedState = 'AL';
        document.getElementById('ir-step3').classList.remove('hidden');
    }""")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_composite_opts_visible_for_hospital(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)
    advance_to_step3(page)

    # Default type is 'hospital' → composite opts should be visible
    opts = page.locator("#ir-composite-opts")
    assert opts.is_visible(), "Composite opts should be visible for hospital type"
    page.close()


def test_composite_opts_hidden_for_specialty(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)

    # Switch to specialty
    page.click("#ir-tg-specialty")
    advance_to_step3(page)

    opts = page.locator("#ir-composite-opts")
    assert not opts.is_visible(), "Composite opts should be hidden for specialty type"
    page.close()


def test_defaults_agg_checked_composite_unchecked(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)
    advance_to_step3(page)

    assert page.is_checked("#ir-agg-toggle"),        "ir-agg-toggle should be checked by default"
    assert not page.is_checked("#ir-composite-toggle"), "ir-composite-toggle should be unchecked by default"
    page.close()


def test_mutual_exclusivity_agg_unchecks_composite(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)
    advance_to_step3(page)

    # Check composite — agg should uncheck
    page.check("#ir-composite-toggle")
    assert not page.is_checked("#ir-agg-toggle"), "Checking composite should uncheck agg"

    # Check agg — composite should uncheck
    page.check("#ir-agg-toggle")
    assert not page.is_checked("#ir-composite-toggle"), "Checking agg should uncheck composite"
    page.close()


def test_add_entity_modal_hidden_by_default(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)
    advance_to_step3(page)

    # Show composite step manually
    page.evaluate("() => document.getElementById('ir-step-composite').classList.remove('hidden')")

    modal = page.locator("#add-entity-modal")
    assert modal.get_attribute("style", timeout=2000) is not None
    style = modal.get_attribute("style") or ""
    assert "display:none" in style.replace(" ", "") or "display: none" in style, \
        "Add Entity modal should be hidden by default"
    page.close()


def test_add_entity_modal_opens_on_link_click(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)
    advance_to_step3(page)
    page.evaluate("() => document.getElementById('ir-step-composite').classList.remove('hidden')")

    # Stub out openAddEntityModal to avoid network calls
    page.evaluate("() => window.openAddEntityModal = function() { document.getElementById('add-entity-modal').style.display='flex'; }")
    page.click("#ir-add-entity-link")

    modal = page.locator("#add-entity-modal")
    style = modal.get_attribute("style") or ""
    assert "flex" in style, "Modal should be displayed as flex after clicking the link"
    page.close()


def test_switching_back_to_hospital_restores_defaults(browser_ctx):
    ctx, base_url = browser_ctx
    page = ctx.new_page()
    navigate_to_individual(page, base_url)

    # Switch specialty then back to hospital
    page.click("#ir-tg-specialty")
    page.click("#ir-tg-hospital")
    advance_to_step3(page)

    opts = page.locator("#ir-composite-opts")
    assert opts.is_visible(), "Composite opts re-shown after switching back to hospital"
    page.close()
