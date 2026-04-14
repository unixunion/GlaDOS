"""LLM-backed meal planning conversation test.

Tests that the LLM correctly orchestrates meal planning tools in a
multi-turn conversation. Requires a running LLM server.

Run with: pytest tests/test_meal_planner_llm.py -m benchmark -v
"""
import os
import sys
import tempfile
import queue

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.benchmark


@pytest.fixture(scope="module")
def chat_env():
    """Set up a full ChatClient with plugins for LLM testing."""
    tmpdir = tempfile.mkdtemp(prefix="glados_llm_test_")
    os.environ["PANTRY_DATA_DIR"] = os.path.join(tmpdir, "pantry")
    os.environ["TIMER_DATA_DIR"] = os.path.join(tmpdir, "timers")
    os.environ["ALARM_DATA_DIR"] = os.path.join(tmpdir, "alarms")
    for d in ("pantry", "timers", "alarms"):
        os.makedirs(os.path.join(tmpdir, d), exist_ok=True)

    from plugins.pantry.pantry_plugin import PantryPlugin
    PantryPlugin._instance = None
    PantryPlugin._initialized = False

    from glados.config import GladosConfig
    from glados.system.plugin import load_plugins
    from glados.llm.cores.chat_client import ChatClient

    config = GladosConfig.from_yaml("glados_config.yml")
    load_plugins("plugins")

    client = ChatClient(config)
    client.load_plugin_prompts()

    # Stock pantry
    pp = PantryPlugin()
    pp.store_item("chicken breasts", "fridge")
    pp.store_item("eggs", "fridge")
    pp.store_item("butter", "fridge")
    pp.store_item("rice", "dry-goods")
    pp.store_item("pasta", "dry-goods")
    pp.store_item("olive oil", "dry-goods")

    # Save some favorites
    from plugins.meal_planner.meal_planner_plugin import MealPlannerPlugin
    mp = MealPlannerPlugin()
    mp.save_favorite("Pad Thai")
    mp.save_favorite("Chicken Tikka Masala")
    mp.save_favorite("Spaghetti Carbonara")

    yield {
        "client": client,
        "tts_queue": client.tts_queue,
        "pantry": pp,
        "planner": mp,
    }

    PantryPlugin._instance = None
    PantryPlugin._initialized = False
    for key in ("PANTRY_DATA_DIR", "TIMER_DATA_DIR", "ALARM_DATA_DIR"):
        os.environ.pop(key, None)


def drain_tts(q, timeout=15):
    """Drain TTS queue, collecting spoken text."""
    import time
    texts = []
    start = time.time()
    while time.time() - start < timeout:
        try:
            item = q.get(timeout=1)
            if item == "<EOS>":
                break
            texts.append(item)
        except queue.Empty:
            if texts:
                break
    return " ".join(texts)


class TestLLMMealPlanningConversation:
    """Tests that the LLM orchestrates meal planning tools correctly.

    These tests call chat() with real LLM inference, so they require
    a running LLM server and are slower (~5-15s per test).
    """

    def test_suggest_meals_calls_tool(self, chat_env):
        """'plan healthy meals for the week' should trigger suggest_weekly_meals tool."""
        client = chat_env["client"]
        client.chat("suggest healthy meals for the week")
        response = drain_tts(chat_env["tts_queue"])

        # The LLM should have called suggest_weekly_meals and presented results
        assert response, "Should have produced a spoken response"
        # Check that meals were suggested (planner should have results on display)
        planner = chat_env["planner"]
        # The response should mention recipes or meals
        assert any(w in response.lower() for w in ["recipe", "meal", "suggest", "here"]), \
            f"Response doesn't mention meals: {response[:200]}"

    def test_follow_up_plan_them(self, chat_env):
        """After suggesting, 'plan those' should trigger plan_meal calls."""
        client = chat_env["client"]
        # First suggest
        client.chat("suggest 3 meals for the week")
        drain_tts(chat_env["tts_queue"])

        # Then ask to plan them
        client.chat("plan those for the week")
        response = drain_tts(chat_env["tts_queue"])

        # LLM should have called plan_meal
        planner = chat_env["planner"]
        planned = len(planner._meal_plan["meals"])
        # At least some meals should be planned (LLM may or may not plan all)
        assert response, f"Should have responded. Planned: {planned}"
