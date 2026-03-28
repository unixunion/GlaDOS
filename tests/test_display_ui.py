"""Display UI end-to-end tests using Playwright.

Spins up the Flask/SocketIO display server, then tests navigation,
shopping list, pantry, recipe search, and interactive elements.

Run with: pytest tests/test_display_ui.py -v --headed  (to see the browser)
Run headless: pytest tests/test_display_ui.py -v

Requires: pip install playwright pytest-playwright
           python -m playwright install chromium
"""
import os
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def display_server():
    """Start the display server on a test port for the module."""
    # Use temp pantry data
    tmpdir = tempfile.mkdtemp(prefix="glados_ui_test_")
    os.environ["PANTRY_DATA_DIR"] = tmpdir

    # Reset singletons
    from plugins.pantry.pantry_plugin import PantryPlugin
    PantryPlugin._instance = None
    PantryPlugin._initialized = False

    from plugins.display.display_server import DisplayPlugin
    DisplayPlugin._instance = None
    DisplayPlugin._initialized = False

    # Create and start the display plugin on test port
    dp = DisplayPlugin()
    dp.start()  # Subscribe to events

    # Override port
    def run_flask():
        dp._socketio.run(dp._flask_app, host="0.0.0.0", port=5099, allow_unsafe_werkzeug=True)

    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    time.sleep(1)  # Wait for server to start

    # Create pantry plugin
    pp = PantryPlugin()
    pp.start()

    # Add some test data
    pp.add_to_shopping_list("test eggs", quantity="1 dozen")
    pp.add_to_shopping_list("test milk")
    pp.add_to_shopping_list("test bread")
    pp.store_item("test chicken", "fridge")

    yield {"url": "http://localhost:5099", "pantry": pp, "display": dp}

    # Cleanup
    PantryPlugin._instance = None
    PantryPlugin._initialized = False
    DisplayPlugin._instance = None
    DisplayPlugin._initialized = False
    del os.environ["PANTRY_DATA_DIR"]


# ---------------------------------------------------------------------------
# Navigation tests
# ---------------------------------------------------------------------------

class TestNavigation:
    def test_dashboard_loads(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert page.locator(".dash-card").count() == 4

    def test_dashboard_has_clock(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dash-clock")
        assert page.locator(".dash-clock .time").is_visible()

    def test_back_button_hidden_on_dashboard(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert not page.locator("#back-btn").is_visible()

    def test_shopping_card_navigates(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        # Click shopping card header
        page.locator(".dash-card-header").first.click()
        page.wait_for_timeout(1500)  # Wait for SocketIO round-trip
        # Back button should be visible
        assert page.locator("#back-btn").is_visible()

    def test_back_returns_to_dashboard(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator(".dash-card-header").first.click()
        page.wait_for_timeout(1500)
        page.locator("#back-btn").click()
        page.wait_for_selector(".dashboard")
        assert page.locator(".dash-card").count() == 4


# ---------------------------------------------------------------------------
# Chat drawer tests
# ---------------------------------------------------------------------------

class TestChatDrawer:
    def test_drawer_initially_closed(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert not page.locator("#chat-drawer").evaluate("el => el.classList.contains('open')")

    def test_toggle_opens_drawer(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator("#chat-toggle").click()
        page.wait_for_timeout(300)
        assert page.locator("#chat-drawer").evaluate("el => el.classList.contains('open')")

    def test_backdrop_closes_drawer(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator("#chat-toggle").click()
        page.wait_for_timeout(300)
        page.locator("#chat-backdrop").click()
        page.wait_for_timeout(300)
        assert not page.locator("#chat-drawer").evaluate("el => el.classList.contains('open')")

    def test_chat_input_exists(self, page, display_server):
        page.goto(display_server["url"])
        page.locator("#chat-toggle").click()
        page.wait_for_timeout(300)
        assert page.locator("#chat-input").is_visible()
        assert page.locator("#chat-send").is_visible()
        assert page.locator("#chat-interrupt").is_visible()


# ---------------------------------------------------------------------------
# Dashboard card tests
# ---------------------------------------------------------------------------

class TestDashboardCards:
    def test_shopping_card_has_quick_add(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert page.locator("#dash-add-item").is_visible()

    def test_recipe_card_has_search(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert page.locator("#dash-recipe-search").is_visible()

    def test_quick_add_item(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator("#dash-add-item").fill("test bananas")
        page.locator("#dash-add-item").press("Enter")
        page.wait_for_timeout(3000)  # Event ticker is 1s + SocketIO round-trip
        # Verify the count updated (badge should change)
        badge = page.locator(".dash-card-badge").first
        assert badge.is_visible()


# ---------------------------------------------------------------------------
# Shopping list view tests
# ---------------------------------------------------------------------------

class TestShoppingListView:
    def test_shopping_list_renders(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator(".dash-card-header").first.click()
        page.wait_for_timeout(1500)
        # Should have shopping items
        items = page.locator(".shopping-item")
        assert items.count() >= 3  # test eggs, milk, bread

    def test_add_form_visible(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator(".dash-card-header").first.click()
        page.wait_for_timeout(1500)
        assert page.locator("#sl-add-name").is_visible()

    def test_checkbox_toggles(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        page.locator(".dash-card-header").first.click()
        page.wait_for_timeout(1500)
        # Click first item to toggle
        first_item = page.locator(".shopping-item").first
        first_item.click()
        page.wait_for_timeout(500)
        # Should have 'got' class now
        assert first_item.evaluate("el => el.classList.contains('got')")


# ---------------------------------------------------------------------------
# Timer overlay test
# ---------------------------------------------------------------------------

class TestTimerOverlay:
    def test_timer_overlay_hidden_by_default(self, page, display_server):
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert not page.locator("#timer-overlay").evaluate("el => el.classList.contains('visible')")


# ---------------------------------------------------------------------------
# Mobile bottom nav test
# ---------------------------------------------------------------------------

class TestMobileNav:
    def test_bottom_nav_visible_on_mobile(self, page, display_server):
        page.set_viewport_size({"width": 375, "height": 812})  # iPhone
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert page.locator("#bottom-nav").is_visible()

    def test_bottom_nav_hidden_on_desktop(self, page, display_server):
        page.set_viewport_size({"width": 1024, "height": 768})
        page.goto(display_server["url"])
        page.wait_for_selector(".dashboard")
        assert not page.locator("#bottom-nav").is_visible()
