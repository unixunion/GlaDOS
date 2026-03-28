"""Pantry plugin test suite — shopping list, pantry, sub-contexts, NLP extraction.

Uses a temporary data directory so tests never affect real shopping/pantry data.

Run with: pytest tests/test_pantry.py -v
"""
import os
import queue
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module", autouse=True)
def _use_temp_data_dir():
    """Point PantryPlugin at a temp directory for all tests in this module."""
    with tempfile.TemporaryDirectory(prefix="glados_pantry_test_") as tmpdir:
        os.environ["PANTRY_DATA_DIR"] = tmpdir
        # Reset singleton so it re-inits with the new data dir
        from plugins.pantry.pantry_plugin import PantryPlugin
        PantryPlugin._instance = None
        PantryPlugin._initialized = False
        yield tmpdir
        # Clean up singleton
        PantryPlugin._instance = None
        PantryPlugin._initialized = False
        del os.environ["PANTRY_DATA_DIR"]


@pytest.fixture(scope="module")
def plugin():
    from plugins.pantry.pantry_plugin import PantryPlugin
    return PantryPlugin()


@pytest.fixture
def tts_queue():
    return queue.Queue()


# ---------------------------------------------------------------------------
# Shopping list operations
# ---------------------------------------------------------------------------

class TestShoppingListOperations:
    def test_add_item(self, plugin):
        result = plugin.add_to_shopping_list("test eggs")
        assert result["status"] == "added"
        assert result["item"] == "test eggs"
        assert result["category"] == "dairy"

    def test_add_duplicate(self, plugin):
        plugin.add_to_shopping_list("test milk")
        result = plugin.add_to_shopping_list("test milk")
        assert result["status"] == "exists"

    def test_remove_item(self, plugin):
        plugin.add_to_shopping_list("test butter")
        result = plugin.remove_from_shopping_list("test butter")
        assert result["status"] == "success"

    def test_remove_nonexistent(self, plugin):
        result = plugin.remove_from_shopping_list("nonexistent item xyz")
        assert result["status"] == "not_found"

    def test_show_list(self, plugin):
        result = plugin.show_shopping_list()
        assert result["status"] == "success"
        assert "count" in result

    def test_add_with_recurring(self, plugin):
        result = plugin.add_to_shopping_list("test bread", recurring_days=7)
        assert result["status"] == "added"
        assert result["recurring_days"] == 7
        rules = plugin._shopping_list["recurring_rules"]
        assert any(r["item_name"] == "test bread" for r in rules)

    def test_complete_shopping(self, plugin):
        # Clear existing items first
        plugin._shopping_list["items"] = []
        plugin.add_to_shopping_list("avocados")
        plugin.add_to_shopping_list("mangoes")
        result = plugin.complete_shopping(except_items="mangoes")
        assert result["moved"] >= 1
        assert "mangoes" in result["remaining_items"]

    def test_category_assignment(self):
        from plugins.pantry.pantry_plugin import _categorize_item
        assert _categorize_item("whole milk") == "dairy"
        assert _categorize_item("chicken breast") == "meat"
        assert _categorize_item("bananas") == "produce"
        assert _categorize_item("wholemeal bread") == "bakery"
        assert _categorize_item("batteries") == "other"


# ---------------------------------------------------------------------------
# Pantry operations
# ---------------------------------------------------------------------------

class TestPantryOperations:
    def test_store_item(self, plugin):
        result = plugin.store_item("test ham", "fridge")
        assert result["status"] == "success"
        assert result["location"] == "Fridge"

    def test_find_item(self, plugin):
        plugin.store_item("test flour", "dry goods cupboard")
        result = plugin.find_item("test flour")
        assert result["status"] == "found"
        assert len(result["matches"]) >= 1

    def test_find_nonexistent(self, plugin):
        result = plugin.find_item("unicorn meat")
        assert result["status"] == "not_found"

    def test_set_expiry(self, plugin):
        plugin.store_item("test yogurt", "fridge")
        result = plugin.set_expiry("test yogurt", "2026-04-15")
        assert result["status"] == "success"
        assert result["expires"] == "2026-04-15"

    def test_check_expiring(self, plugin):
        result = plugin.check_expiring(days=365)
        assert result["status"] == "success"
        assert isinstance(result["expiring"], list)

    def test_show_pantry(self, plugin):
        result = plugin.show_pantry()
        assert result["status"] == "success"
        assert "total_items" in result

    def test_manage_locations_add(self, plugin):
        result = plugin.manage_pantry_locations("add", "Test Garage")
        assert result["status"] == "success"

    def test_manage_locations_remove(self, plugin):
        plugin.manage_pantry_locations("add", "Test Shed")
        result = plugin.manage_pantry_locations("remove", "Test Shed")
        assert result["status"] == "success"

    def test_store_removes_from_shopping_list(self, plugin):
        plugin.add_to_shopping_list("test bacon")
        plugin.store_item("test bacon", "fridge")
        match = plugin._find_shopping_item("test bacon")
        assert match is None  # Should have been removed from shopping list


# ---------------------------------------------------------------------------
# NLP extraction
# ---------------------------------------------------------------------------

class TestNLPExtraction:
    def test_add_extract(self):
        from plugins.pantry.pantry_plugin import _add_to_list_nlp_extract
        result = _add_to_list_nlp_extract("add eggs to the shopping list")
        assert result.get("item") == "eggs"

    def test_add_out_of(self):
        from plugins.pantry.pantry_plugin import _add_to_list_nlp_extract
        result = _add_to_list_nlp_extract("we're out of butter")
        assert "butter" in result.get("item", "")

    def test_add_recurring_word_number(self):
        from plugins.pantry.pantry_plugin import _add_to_list_nlp_extract
        result = _add_to_list_nlp_extract("we buy bread every two weeks")
        assert result.get("recurring_days") == 14
        assert "bread" in result.get("item", "")

    def test_complete_except(self):
        from plugins.pantry.pantry_plugin import _complete_shopping_nlp_extract
        result = _complete_shopping_nlp_extract("we got everything except eggs and butter")
        assert "eggs" in result.get("except_items", "")
        assert "butter" in result.get("except_items", "")

    def test_store_extract(self):
        from plugins.pantry.pantry_plugin import _store_item_nlp_extract
        result = _store_item_nlp_extract("I put the chicken in freezer drawer 2")
        assert "chicken" in result.get("item", "")
        assert "freezer" in result.get("location", "")

    def test_expiry_extract(self):
        from plugins.pantry.pantry_plugin import _set_expiry_nlp_extract
        result = _set_expiry_nlp_extract("the bacon expires on the 24th")
        assert "bacon" in result.get("item", "")
        assert result.get("expires") is not None

    def test_find_extract(self):
        from plugins.pantry.pantry_plugin import _find_item_nlp_extract
        result = _find_item_nlp_extract("where is the flour")
        assert "flour" in result.get("item", "")

    def test_find_do_we_have(self):
        from plugins.pantry.pantry_plugin import _find_item_nlp_extract
        result = _find_item_nlp_extract("do we have eggs")
        assert "eggs" in result.get("item", "")


# ---------------------------------------------------------------------------
# Shopping sub-contexts
# ---------------------------------------------------------------------------

class TestShoppingSubContexts:
    def test_enter_planning_mode(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = None
        ctx = ChatContext(user_text=" Shopping mode.", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert plugin._shopping_mode == "planning"
        assert ctx.handled is True

    def test_add_item_in_planning(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = "planning"
        ctx = ChatContext(user_text=" bananas", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert ctx.handled is True
        msg = tts_queue.get()
        assert "bananas" in msg.lower()

    def test_quantity_update_word_number(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = "planning"
        # First add an item so _last_added_item is set
        ctx1 = ChatContext(user_text=" test apples", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx1)
        while not tts_queue.empty():
            tts_queue.get()

        ctx2 = ChatContext(user_text=" make that six", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx2)
        assert ctx2.handled is True
        assert plugin._last_added_item["quantity"] == "6"

    def test_multi_item_and(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = "planning"
        ctx = ChatContext(user_text=" lettuce and a pineapple", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert ctx.handled is True
        msg = tts_queue.get()
        assert "lettuce" in msg.lower()
        assert "pineapple" in msg.lower()

    def test_exit_planning(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = "planning"
        ctx = ChatContext(user_text=" End planning mode.", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert plugin._shopping_mode is None
        assert ctx.handled is True

    def test_enter_post_shopping(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = None
        ctx = ChatContext(user_text=" back from shopping", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert plugin._shopping_mode == "post_shopping"
        assert ctx.handled is True

    def test_exit_post_shopping(self, plugin, tts_queue):
        from glados.llm.chat_hooks import ChatContext
        plugin._shopping_mode = "post_shopping"
        ctx = ChatContext(user_text=" done", activity=None, session_id="t", tts_queue=tts_queue)
        plugin._shopping_context_hook(ctx)
        assert plugin._shopping_mode is None
        assert ctx.handled is True

    def test_voice_text_cleaning(self, plugin):
        assert plugin._clean_voice_text(" Shopping mode.") == "shopping mode"
        assert plugin._clean_voice_text("  Done! ") == "done"
        assert plugin._clean_voice_text("That's everything.") == "that's everything"


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

class TestDateParsing:
    def test_iso_format(self):
        from plugins.pantry.pantry_plugin import _parse_expiry_date
        assert _parse_expiry_date("2026-04-15") == "2026-04-15"

    def test_tomorrow(self):
        from plugins.pantry.pantry_plugin import _parse_expiry_date
        from datetime import date, timedelta
        assert _parse_expiry_date("tomorrow") == (date.today() + timedelta(days=1)).isoformat()

    def test_day_only(self):
        from plugins.pantry.pantry_plugin import _parse_expiry_date
        result = _parse_expiry_date("the 24th")
        assert result is not None
        assert "-24" in result
