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
    """Point all plugins at temp directories so tests never affect real data."""
    with tempfile.TemporaryDirectory(prefix="glados_test_") as tmpdir:
        os.environ["PANTRY_DATA_DIR"] = tmpdir
        os.environ["TIMER_DATA_DIR"] = os.path.join(tmpdir, "timers")
        os.environ["ALARM_DATA_DIR"] = os.path.join(tmpdir, "alarms")
        os.makedirs(os.path.join(tmpdir, "timers"), exist_ok=True)
        os.makedirs(os.path.join(tmpdir, "alarms"), exist_ok=True)
        # Reset singleton so it re-inits with the new data dir
        from plugins.pantry.pantry_plugin import PantryPlugin
        PantryPlugin._instance = None
        PantryPlugin._initialized = False
        yield tmpdir
        # Clean up singletons
        PantryPlugin._instance = None
        PantryPlugin._initialized = False
        for key in ("PANTRY_DATA_DIR", "TIMER_DATA_DIR", "ALARM_DATA_DIR"):
            os.environ.pop(key, None)


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


# ---------------------------------------------------------------------------
# Item type classification
# ---------------------------------------------------------------------------

class TestItemTypeClassification:
    def test_ready_meal_keyword_match(self):
        from plugins.pantry.pantry_plugin import _classify_item_type
        assert _classify_item_type("lasagna") == "ready_meal"
        assert _classify_item_type("frozen pizza") == "ready_meal"
        assert _classify_item_type("leftover soup") == "ready_meal"
        assert _classify_item_type("chicken tikka") == "ready_meal"
        assert _classify_item_type("shepherd's pie") == "ready_meal"
        assert _classify_item_type("mac and cheese") == "ready_meal"

    def test_ingredient_classification(self):
        from plugins.pantry.pantry_plugin import _classify_item_type
        assert _classify_item_type("chicken") == "ingredient"
        assert _classify_item_type("flour") == "ingredient"
        assert _classify_item_type("eggs") == "ingredient"
        assert _classify_item_type("butter") == "ingredient"
        assert _classify_item_type("rice") == "ingredient"
        assert _classify_item_type("onion") == "ingredient"

    def test_ready_meal_fuzzy_match(self):
        from plugins.pantry.pantry_plugin import _classify_item_type
        assert _classify_item_type("beef lasagne") == "ready_meal"
        assert _classify_item_type("chicken curry") == "ready_meal"
        assert _classify_item_type("vegetable stew") == "ready_meal"

    def test_store_item_nlp_extract_with_type(self):
        from plugins.pantry.pantry_plugin import _store_item_nlp_extract
        result = _store_item_nlp_extract("put the lasagna as a meal in the freezer")
        assert result.get("item_type") == "ready_meal"
        assert result.get("item") == "lasagna"

    def test_store_item_nlp_extract_as_ingredient(self):
        from plugins.pantry.pantry_plugin import _store_item_nlp_extract
        result = _store_item_nlp_extract("store the chicken as an ingredient in the fridge")
        assert result.get("item_type") == "ingredient"

    def test_store_item_nlp_extract_no_type(self):
        from plugins.pantry.pantry_plugin import _store_item_nlp_extract
        result = _store_item_nlp_extract("put the milk in the fridge")
        assert "item_type" not in result
        assert result.get("item") == "milk"


class TestSuggestMealsSeparation:
    """Test that suggest_meals separates ready meals from ingredients."""

    @pytest.fixture
    def plugin(self, tmp_path):
        os.environ["PANTRY_DATA_DIR"] = str(tmp_path)
        from plugins.pantry.pantry_plugin import PantryPlugin
        PantryPlugin._instance = None
        pp = PantryPlugin()
        # Add a mix of items
        pp.store_item("lasagna", "fridge", item_type="ready_meal")
        pp.store_item("frozen pizza", "freezer-1", item_type="ready_meal")
        pp.store_item("chicken", "fridge", item_type="ingredient")
        pp.store_item("eggs", "fridge", item_type="ingredient")
        pp.store_item("flour", "dry-goods", item_type="ingredient")
        return pp

    def test_ready_meals_separated(self, plugin):
        result = plugin.suggest_meals_from_pantry()
        ready = result.get("ready_meals", [])
        ready_names = [m["name"] for m in ready]
        assert "lasagna" in ready_names
        assert "frozen pizza" in ready_names
        assert "chicken" not in ready_names

    def test_message_includes_ready_meals(self, plugin):
        result = plugin.suggest_meals_from_pantry()
        msg = result.get("message", "")
        assert "Ready to eat" in msg


class TestSetItemType:
    """Test reclassifying pantry items."""

    @pytest.fixture
    def plugin(self, tmp_path):
        os.environ["PANTRY_DATA_DIR"] = str(tmp_path)
        from plugins.pantry.pantry_plugin import PantryPlugin
        PantryPlugin._instance = None
        pp = PantryPlugin()
        pp.store_item("chicken", "fridge")
        return pp

    def test_reclassify_to_ready_meal(self, plugin):
        result = plugin.set_item_type("chicken", "ready_meal")
        assert result["status"] == "success"
        assert result["item_type"] == "ready_meal"
        item = plugin._find_pantry_items("chicken")[0]
        assert item["item_type"] == "ready_meal"

    def test_reclassify_to_ingredient(self, plugin):
        plugin.set_item_type("chicken", "ready_meal")
        result = plugin.set_item_type("chicken", "ingredient")
        assert result["status"] == "success"
        assert result["item_type"] == "ingredient"

    def test_invalid_type(self, plugin):
        result = plugin.set_item_type("chicken", "snack")
        assert result["status"] == "error"

    def test_item_not_found(self, plugin):
        result = plugin.set_item_type("unicorn", "ready_meal")
        assert result["status"] == "not_found"


class TestStoreItemWithType:
    """Test store_item with explicit item_type parameter."""

    @pytest.fixture
    def plugin(self, tmp_path):
        os.environ["PANTRY_DATA_DIR"] = str(tmp_path)
        from plugins.pantry.pantry_plugin import PantryPlugin
        PantryPlugin._instance = None
        return PantryPlugin()

    def test_explicit_ready_meal(self, plugin):
        result = plugin.store_item("chicken tikka", "fridge", item_type="ready_meal")
        assert result["item_type"] == "ready_meal"

    def test_explicit_ingredient(self, plugin):
        result = plugin.store_item("lasagna sheets", "dry-goods", item_type="ingredient")
        assert result["item_type"] == "ingredient"

    def test_auto_classify_ready_meal(self, plugin):
        result = plugin.store_item("frozen pizza", "freezer-1")
        assert result["item_type"] == "ready_meal"

    def test_auto_classify_ingredient(self, plugin):
        result = plugin.store_item("butter", "fridge")
        assert result["item_type"] == "ingredient"


class TestFuzzyMatchingModes:
    """Test strict vs loose pantry matching to avoid false positives like
    'chicken thighs' matching 'chicken breasts'."""

    def test_loose_match_chicken(self, plugin):
        """Loose mode: 'chicken' should match 'chicken breasts' (substring)."""
        plugin.store_item("chicken breasts", "fridge")
        matches = plugin._find_pantry_items("chicken", strict=False)
        assert len(matches) >= 1

    def test_strict_no_match_different_cuts(self, plugin):
        """Strict mode: 'chicken thighs' should NOT match 'chicken breasts'."""
        plugin.store_item("chicken breasts", "fridge")
        matches = plugin._find_pantry_items("chicken thighs", strict=True)
        assert len(matches) == 0

    def test_strict_match_same_item(self, plugin):
        """Strict mode: 'chicken breasts' should match 'chicken breasts'."""
        plugin.store_item("chicken breasts", "fridge")
        matches = plugin._find_pantry_items("chicken breasts", strict=True)
        assert len(matches) >= 1

    def test_strict_no_match_butter_peanut_butter(self, plugin):
        """Strict mode: 'butter' should NOT match 'peanut butter'."""
        plugin.store_item("peanut butter", "dry-goods")
        matches = plugin._find_pantry_items("butter", strict=True)
        assert len(matches) == 0

    def test_loose_match_butter_peanut_butter(self, plugin):
        """Loose mode: 'butter' DOES match 'peanut butter' (substring)."""
        plugin.store_item("peanut butter", "dry-goods")
        matches = plugin._find_pantry_items("butter", strict=False)
        assert len(matches) >= 1

    def test_strict_close_spelling(self, plugin):
        """Strict mode: 'chicken breast' should match 'chicken breasts' (close spelling)."""
        plugin.store_item("chicken breasts", "fridge")
        matches = plugin._find_pantry_items("chicken breast", strict=True)
        assert len(matches) >= 1
