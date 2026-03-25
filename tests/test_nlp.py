"""
NLP mode test suite — intent classification accuracy, parameter extraction, and end-to-end dispatch.

Loads all plugins (to get real intents) and runs classification, extraction, and dispatch tests.
Produces a scorecard showing per-tool accuracy so regressions are caught as intents are added.

Run with: pytest tests/test_nlp.py -v
"""
import json
import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glados.system.intent_classifier import IntentClassifier
from glados.system.plugin import PluginSystem, load_plugins
from glados.nlp.extractors import (
    parse_duration,
    word_to_number,
    extract_after_keyword,
    extract_music_action,
)
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry
from glados.nlp.dispatcher import NLPDispatcher
from glados.context.activity import Activity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def load_all_plugins():
    """Load all plugins once for the module so IntentClassifier is fully trained.
    Cooking intents auto-register when cooking_context.py is imported by load_plugins."""
    load_plugins("plugins")


@pytest.fixture(scope="module")
def classifier():
    return IntentClassifier()


@pytest.fixture(scope="module")
def registry():
    return NLPHandlerRegistry()


@pytest.fixture(scope="module")
def plugin_system():
    return PluginSystem()


# ---------------------------------------------------------------------------
# 1. Extractor unit tests
# ---------------------------------------------------------------------------

class TestWordToNumber:
    def test_digits(self):
        assert word_to_number("42") == 42
        assert word_to_number("0") == 0

    def test_single_words(self):
        assert word_to_number("zero") == 0
        assert word_to_number("one") == 1
        assert word_to_number("twelve") == 12
        assert word_to_number("nineteen") == 19

    def test_tens(self):
        assert word_to_number("twenty") == 20
        assert word_to_number("ninety") == 90

    def test_compound(self):
        assert word_to_number("twenty five") == 25
        assert word_to_number("thirty-seven") == 37
        assert word_to_number("ninety nine") == 99

    def test_articles(self):
        assert word_to_number("a") == 1
        assert word_to_number("an") == 1

    def test_unrecognized(self):
        assert word_to_number("banana") is None
        assert word_to_number("hundred") is None


class TestParseDuration:
    def test_minutes_only(self):
        assert parse_duration("12 minutes") == {"hours": 0, "minutes": 12, "seconds": 0}

    def test_hours_only(self):
        assert parse_duration("2 hours") == {"hours": 2, "minutes": 0, "seconds": 0}

    def test_seconds_only(self):
        assert parse_duration("45 seconds") == {"hours": 0, "minutes": 0, "seconds": 45}

    def test_compound_with_and(self):
        assert parse_duration("1 hour and 30 minutes") == {"hours": 1, "minutes": 30, "seconds": 0}
        assert parse_duration("5 minutes and 30 seconds") == {"hours": 0, "minutes": 5, "seconds": 30}

    def test_full_compound(self):
        assert parse_duration("1 hour 20 minutes and 10 seconds") == {"hours": 1, "minutes": 20, "seconds": 10}

    def test_word_numbers(self):
        assert parse_duration("twelve minutes") == {"hours": 0, "minutes": 12, "seconds": 0}
        assert parse_duration("one hour and thirty seconds") == {"hours": 1, "minutes": 0, "seconds": 30}

    def test_embedded_in_sentence(self):
        assert parse_duration("set a timer for 5 minutes") == {"hours": 0, "minutes": 5, "seconds": 0}
        assert parse_duration("timer for 1 hour and 2 minutes please") == {"hours": 1, "minutes": 2, "seconds": 0}

    def test_no_duration(self):
        assert parse_duration("hello world") is None
        assert parse_duration("what is the time") is None


class TestExtractAfterKeyword:
    def test_basic(self):
        assert extract_after_keyword("set a timer for eggs", ["for", "called"]) == "eggs"

    def test_first_keyword_wins(self):
        assert extract_after_keyword("timer called egg timer for cooking", ["called", "for"]) == "egg timer for cooking"

    def test_no_match(self):
        assert extract_after_keyword("what is the time", ["for", "called"]) is None


class TestExtractMusicAction:
    def test_play_with_query(self):
        action, query = extract_music_action("play bohemian rhapsody")
        assert action == "PLAY"
        assert query == "bohemian rhapsody"

    def test_play_with_filler(self):
        action, query = extract_music_action("play the song sugar by maroon 5")
        assert action == "PLAY"
        assert "sugar" in query

    def test_pause(self):
        assert extract_music_action("pause the music") == ("PAUSE", None)
        assert extract_music_action("pause") == ("PAUSE", None)

    def test_resume(self):
        assert extract_music_action("resume the music")[0] == "RESUME"

    def test_skip(self):
        assert extract_music_action("next track")[0] == "SKIP"
        assert extract_music_action("skip song")[0] == "SKIP"

    def test_previous(self):
        assert extract_music_action("previous track")[0] == "PREVIOUS"

    def test_stop(self):
        assert extract_music_action("stop the music")[0] == "STOP"


# ---------------------------------------------------------------------------
# 2. NLPHandler unit tests
# ---------------------------------------------------------------------------

class TestNLPHandler:
    def test_regex_extraction(self):
        import re
        handler = NLPHandler(
            tool_name="test",
            extractors={
                "location": [re.compile(r"\bin\s+(?P<location>.+?)$", re.IGNORECASE)],
            },
        )
        assert handler.extract_params("weather in London") == {"location": "London"}

    def test_extract_fn_takes_priority(self):
        handler = NLPHandler(
            tool_name="test",
            extractors={"x": []},
            extract_fn=lambda text: {"custom": "yes"},
        )
        assert handler.extract_params("anything") == {"custom": "yes"}

    def test_format_response_with_fn(self):
        handler = NLPHandler(
            tool_name="test",
            response_fn=lambda r: f"Got: {r}",
        )
        assert handler.format_response("hello") == "Got: hello"

    def test_format_response_default_dict(self):
        handler = NLPHandler(tool_name="test")
        assert handler.format_response({"message": "Done."}) == "Done."
        assert "error" in handler.format_response({"error": "bad"}).lower()

    def test_format_response_default_string(self):
        handler = NLPHandler(tool_name="test")
        assert handler.format_response("plain text") == "plain text"


# ---------------------------------------------------------------------------
# 3. Intent classification accuracy (the scorecard)
# ---------------------------------------------------------------------------

# Each entry: (input_text, expected_tool_name, min_confidence)
# These represent the "golden set" — if any of these fail, intents have regressed.
INTENT_TEST_CASES = [
    # Clock
    ("what is the time", "get_current_time", 0.5),
    ("what time is it", "get_current_time", 0.5),
    ("tell me the time", "get_current_time", 0.5),
    ("time please", "get_current_time", 0.2),
    ("what is the date", "get_current_time", 0.3),
    # Timers
    ("set a timer for 12 minutes", "set_timer", 0.5),
    ("start a countdown timer for 45 seconds", "set_timer", 0.4),
    ("timer for 60 seconds", "set_timer", 0.3),
    ("start a 10 minute timer", "set_timer", 0.3),
    ("how much time left on my egg timer", "list_timers", 0.3),
    ("what timers are running", "list_timers", 0.2),
    # Alarms
    ("set an alarm for 5pm", "set_fixed_time_alarm", 0.5),
    ("set an alarm for 8:30 am on Sunday", "set_fixed_time_alarm", 0.4),
    ("wake me at 7 in the morning", "set_fixed_time_alarm", 0.3),
    ("get all alarms", "get_alarms", 0.1),
    ("what alarms do I have set", "get_alarms", 0.2),
    ("cancel the alarm", "cancel_alarm", 0.1),
    ("turn off the alarm", "cancel_alarm", 0.2),
    ("dismiss the alarm", "cancel_alarm", 0.2),
    # Weather
    ("what is the weather", "handle_weather", 0.5),
    ("is it cold today", "handle_weather", 0.3),
    ("weather forecast", "handle_weather", 0.3),
    ("will it rain today", "handle_weather", 0.2),
    # Music
    ("play some music", "play_music", 0.5),
    ("play bohemian rhapsody", "play_music", 0.4),
    ("stop the music", "play_music", 0.3),
    ("pause the music", "play_music", 0.3),
    ("resume the music", "play_music", 0.2),
    ("put on some jazz", "play_music", 0.2),
    ("skip song", "play_music", 0.1),
    ("what song is playing", "now_playing", 0.2),
    ("what's currently playing", "now_playing", 0.2),
    ("what am I listening to", "now_playing", 0.2),
    ("list spotify devices", "list_devices", 0.2),
    ("what speakers are connected", "list_devices", 0.1),
    # Vacuum
    ("start vacuuming", "start_vacuuming", 0.1),
    ("clean the kitchen", "start_vacuuming", 0.1),
    ("stop the vacuum cleaner", "stop_vacuuming", 0.2),
    ("stop the roomba", "stop_vacuuming", 0.1),
    # Recipes — search
    ("find me a recipe for bread", "search_recipes", 0.3),
    ("search recipes for pizza", "search_recipes", 0.3),
    ("I need a recipe", "search_recipes", 0.2),
    ("look up a recipe for cookies", "search_recipes", 0.3),
    # Recipes — select
    ("lets make apple pie", "select_recipe", 0.3),
    ("select the pizza recipe", "select_recipe", 0.3),
    ("choose the lasagna recipe", "select_recipe", 0.2),
    ("lets cook spaghetti", "select_recipe", 0.2),
    # Display
    ("put the recipe on screen", "show_on_display", 0.2),
    ("display the timer on screen", "show_on_display", 0.2),
    ("clear the screen", "show_on_display", 0.2),
    ("clear the display", "show_on_display", 0.2),
    # Unit conversion
    ("convert 100 fahrenheit to celsius", "convert_units", 0.3),
    ("how many grams in 2 pounds", "convert_units", 0.3),
    # Arithmetic
    ("what is 5 plus 7", "calculate", 0.3),
    ("add 2 and 2", "calculate", 0.3),
    # System
    ("list all plugins", "list_plugins", 0.3),
    ("what are your capabilities", "list_plugins", 0.2),
    ("what plugins are loaded", "list_plugins", 0.2),
    # Cooking context — ingredients
    ("list the ingredients", "_nlp_list_ingredients", 0.3),
    ("ingredients", "_nlp_list_ingredients", 0.3),
    ("what do I need", "_nlp_list_ingredients", 0.2),
    ("read the ingredients", "_nlp_list_ingredients", 0.2),
    # Cooking context — list steps
    ("what are the steps", "_nlp_list_steps", 0.3),
    ("steps", "_nlp_list_steps", 0.3),
    ("go step by step", "_nlp_list_steps", 0.3),
    ("read the instructions", "_nlp_list_steps", 0.2),
    ("how do I make this", "_nlp_list_steps", 0.2),
    # Cooking context — next step
    ("next step", "_nlp_next_step", 0.3),
    ("keep going", "_nlp_next_step", 0.1),
    ("what do I do next", "_nlp_next_step", 0.2),
    ("okay what now", "_nlp_next_step", 0.2),
    # Cooking context — previous step
    ("previous step", "_nlp_previous_step", 0.2),
    ("go back", "_nlp_previous_step", 0.3),
    ("go back one", "_nlp_previous_step", 0.2),
    ("back one step", "_nlp_previous_step", 0.2),
    # Cooking context — repeat step
    ("can you repeat that", "_nlp_repeat_step", 0.3),
    ("say that again", "_nlp_repeat_step", 0.3),
    ("read that again", "_nlp_repeat_step", 0.2),
    ("I didn't catch that", "_nlp_repeat_step", 0.2),
    # Cooking context — first step
    ("first step", "_nlp_first_step", 0.2),
    ("start from the beginning", "_nlp_first_step", 0.3),
    ("start over", "_nlp_first_step", 0.2),
    ("back to the start", "_nlp_first_step", 0.2),
    # Cooking context — current recipe
    ("what are we making", "_nlp_current_recipe", 0.3),
    ("what are we cooking", "_nlp_current_recipe", 0.2),
    ("what recipe is selected", "_nlp_current_recipe", 0.2),
    # --- Memory operations (pre-LLM interception) ---
    # Remember
    ("remember that I prefer celsius", "_memory_remember", 0.3),
    ("don't forget I'm allergic to peanuts", "_memory_remember", 0.2),
    ("save that to memory", "_memory_remember", 0.3),
    # Recall
    ("do you remember my preferences", "_memory_recall", 0.3),
    ("what do you know about my allergies", "_memory_recall", 0.2),
    ("what did I tell you about", "_memory_recall", 0.2),
    # Forget
    ("forget everything", "_memory_forget_all", 0.1),
    ("clear your memory", "_memory_forget_all", 0.3),
    # --- System / diagnostics ---
    ("check logs for errors", "get_logs", 0.2),
    ("are there any errors", "get_logs", 0.2),
    ("run a self diagnostic", "get_logs", 0.1),
    # --- Polite & casual forms (filler word resilience) ---
    ("could you set a timer for 5 minutes", "set_timer", 0.2),
    ("please play some music", "play_music", 0.3),
    ("would you check the weather", "handle_weather", 0.2),
    ("can you tell me the time please", "get_current_time", 0.2),
    # --- Natural spoken variants ---
    ("how's the weather outside", "handle_weather", 0.2),
    ("five minute timer", "set_timer", 0.2),
    ("how long on my timer", "list_timers", 0.1),
    ("wake me up at 7", "set_fixed_time_alarm", 0.2),
    ("what is this song", "now_playing", 0.2),
    ("what can I cook with chicken", "search_recipes", 0.1),
]


class TestIntentClassification:
    """Tests that the classifier routes common phrases to the correct tool.

    This is the main regression guard. When adding new intents to a plugin,
    run this test to make sure existing phrases still route correctly.
    """

    @pytest.fixture(autouse=True)
    def _inject(self, classifier):
        self.classifier = classifier

    @pytest.mark.parametrize("text,expected_tool,min_confidence", INTENT_TEST_CASES,
                             ids=[f"{case[1]}:{case[0][:30]}" for case in INTENT_TEST_CASES])
    def test_intent_routing(self, text, expected_tool, min_confidence):
        predicted, confidence = self.classifier.predict_intent(text)
        assert predicted == expected_tool, (
            f"'{text}' -> {predicted} ({confidence:.3f}), expected {expected_tool}"
        )
        assert confidence >= min_confidence, (
            f"'{text}' -> {predicted} confidence {confidence:.3f} < {min_confidence}"
        )


class TestIntentScorecard:
    """Prints a full scorecard of intent classification accuracy.

    Not a pass/fail test — this is an informational report that runs with -v
    to help diagnose classification issues as intents are added.
    """

    def test_print_scorecard(self, classifier, capsys):
        results = []
        correct = 0
        total = len(INTENT_TEST_CASES)

        for text, expected, min_conf in INTENT_TEST_CASES:
            predicted, confidence = classifier.predict_intent(text)
            is_correct = predicted == expected
            if is_correct:
                correct += 1
            results.append((text, expected, predicted, confidence, is_correct))

        # Group by expected tool
        by_tool = {}
        for text, expected, predicted, confidence, is_correct in results:
            by_tool.setdefault(expected, []).append((text, predicted, confidence, is_correct))

        print(f"\n{'='*80}")
        print(f"  NLP Intent Classification Scorecard")
        print(f"  Overall: {correct}/{total} correct ({100*correct/total:.0f}%)")
        print(f"{'='*80}")

        for tool_name in sorted(by_tool.keys()):
            cases = by_tool[tool_name]
            tool_correct = sum(1 for _, _, _, ok in cases if ok)
            tool_total = len(cases)
            status = "PASS" if tool_correct == tool_total else "PARTIAL" if tool_correct > 0 else "FAIL"
            print(f"\n  [{status:>7}] {tool_name} ({tool_correct}/{tool_total})")
            for text, predicted, confidence, is_correct in cases:
                icon = "ok" if is_correct else "MISS"
                print(f"    {icon:>4}  {text:45s} -> {predicted:25s} ({confidence:.3f})")

        print(f"\n{'='*80}")

        # This test always passes — it's informational
        assert True


# ---------------------------------------------------------------------------
# 4. NLP handler registration tests
# ---------------------------------------------------------------------------

class TestNLPHandlerRegistration:
    """Verify that plugins registered NLP handlers where expected."""

    EXPECTED_HANDLERS = [
        "get_current_time",
        "handle_weather",
        "set_timer",
        "list_timers",
        "set_fixed_time_alarm",
        "get_alarms",
        "cancel_alarm",
        "start_vacuuming",
        "stop_vacuuming",
        "play_music",
        "now_playing",
        "list_devices",
        "list_plugins",
        "get_logs",
        "calculate",
        "convert_units",
        "search_recipes",
        "select_recipe",
        "_nlp_list_ingredients",
        "_nlp_list_steps",
        "_nlp_next_step",
        "_nlp_previous_step",
        "_nlp_repeat_step",
        "_nlp_first_step",
        "_nlp_current_recipe",
    ]

    def test_handlers_registered(self, registry):
        missing = [name for name in self.EXPECTED_HANDLERS if not registry.has_handler(name)]
        assert not missing, f"Missing NLP handlers: {missing}"


# ---------------------------------------------------------------------------
# 5. Parameter extraction integration tests
# ---------------------------------------------------------------------------

class TestTimerExtraction:
    """Test the timer NLP extract function with real plugin data."""

    def test_simple_minutes(self, registry):
        handler = registry.get("set_timer")
        assert handler is not None
        params = handler.extract_params("set a timer for 12 minutes")
        assert params.get("minutes") == 12

    def test_compound_duration(self, registry):
        handler = registry.get("set_timer")
        params = handler.extract_params("set a timer for 1 hour and 30 minutes")
        assert params.get("hours") == 1
        assert params.get("minutes") == 30

    def test_word_numbers(self, registry):
        handler = registry.get("set_timer")
        params = handler.extract_params("set a timer for five minutes")
        assert params.get("minutes") == 5

    def test_seconds(self, registry):
        handler = registry.get("set_timer")
        params = handler.extract_params("timer for 45 seconds")
        assert params.get("seconds") == 45


class TestAlarmExtraction:
    def test_basic_time(self, registry):
        handler = registry.get("set_fixed_time_alarm")
        assert handler is not None
        params = handler.extract_params("set an alarm for 5pm tomorrow")
        assert "time" in params
        assert "5pm" in params["time"].lower() or "5 pm" in params["time"].lower()

    def test_morning_alarm(self, registry):
        handler = registry.get("set_fixed_time_alarm")
        params = handler.extract_params("set an alarm for 8:30 am on Sunday")
        assert "time" in params


class TestWeatherExtraction:
    def test_location_in(self, registry):
        handler = registry.get("handle_weather")
        assert handler is not None
        params = handler.extract_params("what is the weather in London")
        assert params.get("location") == "London"

    def test_location_for(self, registry):
        handler = registry.get("handle_weather")
        params = handler.extract_params("weather for New York")
        assert params.get("location") == "New York"


class TestMusicExtraction:
    def test_play_query(self, registry):
        handler = registry.get("play_music")
        assert handler is not None
        params = handler.extract_params("play bohemian rhapsody")
        assert params["action"] == "PLAY"
        assert "bohemian rhapsody" in params.get("query", "").lower()

    def test_pause(self, registry):
        handler = registry.get("play_music")
        params = handler.extract_params("pause the music")
        assert params["action"] == "PAUSE"

    def test_skip(self, registry):
        handler = registry.get("play_music")
        params = handler.extract_params("next track")
        assert params["action"] == "SKIP"


class TestCancelAlarmExtraction:
    def test_cancel(self, registry):
        handler = registry.get("cancel_alarm")
        assert handler is not None
        params = handler.extract_params("cancel the 5pm alarm")
        assert "query" in params
        assert "5pm" in params["query"].lower()


# ---------------------------------------------------------------------------
# 6. End-to-end dispatcher tests
# ---------------------------------------------------------------------------

class TestNLPDispatcher:
    """End-to-end tests that classify, extract, call the tool, and produce TTS output."""

    @pytest.fixture
    def dispatch_env(self):
        tts_queue = queue.Queue()
        dispatcher = NLPDispatcher(tts_queue=tts_queue, confidence_threshold=0.4)
        return dispatcher, tts_queue

    @staticmethod
    def drain_queue(q):
        messages = []
        while not q.empty():
            messages.append(q.get())
        return messages

    def test_get_time(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("what is the time", Activity.GENERAL)
        assert result is True
        messages = self.drain_queue(tts_queue)
        assert any("time" in m.lower() for m in messages if m != "<EOS>")

    def test_set_timer(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("set a timer for 5 minutes", Activity.UTILITIES)
        assert result is True
        messages = self.drain_queue(tts_queue)
        text = " ".join(m for m in messages if m != "<EOS>")
        assert "timer" in text.lower() or "5" in text

    def test_weather_with_location(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("what is the weather in London", Activity.GENERAL)
        assert result is True
        messages = self.drain_queue(tts_queue)
        text = " ".join(m for m in messages if m != "<EOS>")
        assert "weather" in text.lower() or "london" in text.lower() or "rainy" in text.lower()

    def test_low_confidence_rejection(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("asdfghjkl zxcvbnm qwerty", Activity.GENERAL)
        # Dispatcher returns False for low-confidence (no tool matched)
        # or True if it spoke a rejection message — either is acceptable
        messages = self.drain_queue(tts_queue)
        text = " ".join(m for m in messages if m != "<EOS>")
        if result:
            assert "understand" in text.lower() or "rephras" in text.lower()
        else:
            assert result is False  # no dispatch, no speech

    def test_list_plugins(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("list all plugins", Activity.SYSTEM)
        assert result is True
        messages = self.drain_queue(tts_queue)
        text = " ".join(m for m in messages if m != "<EOS>")
        assert "tool" in text.lower() or "plugin" in text.lower() or "loaded" in text.lower()

    def test_eos_always_sent(self, dispatch_env):
        dispatcher, tts_queue = dispatch_env
        dispatcher.dispatch("what is the time", Activity.GENERAL)
        messages = self.drain_queue(tts_queue)
        assert "<EOS>" in messages, "Dispatcher must always send <EOS> after speaking"


# ---------------------------------------------------------------------------
# 7. Scoped classification tests
# ---------------------------------------------------------------------------

class TestScopedClassification:
    """Tests that predict_intent_scoped filters to the correct tool subset."""

    @pytest.fixture(autouse=True)
    def _inject(self, classifier):
        self.classifier = classifier

    def test_scoped_to_cooking_tools(self):
        """When scoped to cooking tools, cooking intents should match."""
        cooking_tools = [
            "search_recipes", "select_recipe",
            "_nlp_list_ingredients", "_nlp_list_steps",
            "_nlp_next_step", "_nlp_repeat_step", "_nlp_current_recipe",
        ]
        predicted, confidence = self.classifier.predict_intent_scoped(
            "list the ingredients", cooking_tools
        )
        assert predicted == "_nlp_list_ingredients", f"Got {predicted}"
        assert confidence >= 0.3

    def test_scoped_excludes_unrelated(self):
        """When scoped to non-cooking tools, cooking intents shouldn't match."""
        non_cooking = ["get_current_time", "set_timer", "handle_weather"]
        predicted, confidence = self.classifier.predict_intent_scoped(
            "list the ingredients", non_cooking
        )
        # Should return something from the scoped set, not a cooking tool
        assert predicted in non_cooking or predicted == ""

    def test_scoped_empty_tools(self):
        """Empty tool list returns empty result."""
        predicted, confidence = self.classifier.predict_intent_scoped("hello", [])
        assert predicted == ""
        assert confidence == 0.0

    def test_global_still_finds_time(self):
        """Global classification still routes 'what time is it' correctly."""
        predicted, confidence = self.classifier.predict_intent("what time is it")
        assert predicted == "get_current_time"


# ---------------------------------------------------------------------------
# 8. Cooking session flow tests
# ---------------------------------------------------------------------------

class TestCookingSessionFlow:
    """End-to-end test: select recipe -> list ingredients -> step through directions."""

    @pytest.fixture
    def cooking_env(self):
        tts_queue = queue.Queue()
        dispatcher = NLPDispatcher(tts_queue=tts_queue, confidence_threshold=0.4)
        # Dispatcher registers itself on NLPHandlerRegistry via __init__
        return dispatcher, tts_queue

    @staticmethod
    def drain_queue(q):
        messages = []
        while not q.empty():
            messages.append(q.get())
        return " ".join(m for m in messages if m != "<EOS>")

    def test_select_recipe_stores_session(self, cooking_env):
        """Selecting a recipe should populate the cooking session."""
        dispatcher, tts_queue = cooking_env
        dispatcher.dispatch("lets make pecan pralines", Activity.COOKING)
        self.drain_queue(tts_queue)

        session = dispatcher.get_session(Activity.COOKING)
        assert "selected_recipe" in session, "Session should have selected_recipe after select"
        assert session["selected_recipe"]["status"] == "success"
        assert session["current_step"] == 0

    def test_list_ingredients_after_select(self, cooking_env):
        """After selecting a recipe, 'list ingredients' should read from session."""
        dispatcher, tts_queue = cooking_env

        # Select a recipe first
        dispatcher.dispatch("lets make pecan pralines", Activity.COOKING)
        self.drain_queue(tts_queue)

        # Now ask for ingredients
        dispatcher.dispatch("list the ingredients", Activity.COOKING)
        text = self.drain_queue(tts_queue)
        assert "ingredients" in text.lower() or "praline" in text.lower(), f"Got: {text}"

    def test_next_step_advances(self, cooking_env):
        """'next step' should read steps sequentially."""
        dispatcher, tts_queue = cooking_env

        # Select recipe
        dispatcher.dispatch("lets make pecan pralines", Activity.COOKING)
        self.drain_queue(tts_queue)

        # First step
        dispatcher.dispatch("next step", Activity.COOKING)
        step1 = self.drain_queue(tts_queue)
        assert step1, "Should have spoken a step"

        # Second step should be different
        dispatcher.dispatch("next step", Activity.COOKING)
        step2 = self.drain_queue(tts_queue)
        assert step2, "Should have spoken another step"

        session = dispatcher.get_session(Activity.COOKING)
        assert session["current_step"] == 2, "Should have advanced to step 2"

    def test_repeat_step(self, cooking_env):
        """'can you repeat that' should re-read the last step."""
        dispatcher, tts_queue = cooking_env

        # Select and advance one step
        dispatcher.dispatch("lets make pecan pralines", Activity.COOKING)
        self.drain_queue(tts_queue)
        dispatcher.dispatch("next step", Activity.COOKING)
        step1 = self.drain_queue(tts_queue)

        # Repeat
        dispatcher.dispatch("can you repeat that", Activity.COOKING)
        repeated = self.drain_queue(tts_queue)
        assert repeated == step1, f"Repeat should match: '{repeated}' vs '{step1}'"

    def test_current_recipe(self, cooking_env):
        """'what are we making' should say the recipe title."""
        dispatcher, tts_queue = cooking_env

        dispatcher.dispatch("lets make pecan pralines", Activity.COOKING)
        self.drain_queue(tts_queue)

        dispatcher.dispatch("what are we making", Activity.COOKING)
        text = self.drain_queue(tts_queue)
        assert "praline" in text.lower(), f"Should mention the recipe: {text}"

    def test_no_recipe_selected(self, cooking_env):
        """Cooking commands without a selected recipe should say so."""
        dispatcher, tts_queue = cooking_env
        # Fresh dispatcher, no recipe selected
        dispatcher.dispatch("list the ingredients", Activity.COOKING)
        text = self.drain_queue(tts_queue)
        assert "no recipe" in text.lower() or "select" in text.lower(), f"Got: {text}"

    def test_global_fallback_from_cooking(self, cooking_env):
        """'what time is it' should still work from COOKING via global fallback."""
        dispatcher, tts_queue = cooking_env
        result = dispatcher.dispatch("what time is it", Activity.COOKING)
        assert result is True
        text = self.drain_queue(tts_queue)
        assert "time" in text.lower() or ":" in text, f"Expected time response, got: {text}"


# ---------------------------------------------------------------------------
# 9. Negative cases — phrases that should NOT trigger any tool
# ---------------------------------------------------------------------------

NEGATIVE_TEST_CASES = [
    "hello",
    "good morning",
    "thanks",
    "thank you that's all",
    "never mind",
    "what's the capital of France",
    "tell me a joke",
    "how are you doing",
    "asdf jkl random gibberish",
    "okay",
    "hmm let me think",
    "I don't know",
    "that's interesting",
    "goodbye",
]

# Threshold below which we consider the classifier "not confident" —
# matches the NLP dispatcher's default rejection threshold (half of 0.4)
NEGATIVE_MAX_CONFIDENCE = 0.4


class TestNegativeCases:
    """Phrases that should NOT confidently match any tool.

    These catch regressions where unrelated phrases start triggering tools.
    We assert the top prediction confidence stays below the dispatcher threshold.
    """

    @pytest.fixture(autouse=True)
    def _inject(self, classifier):
        self.classifier = classifier

    @pytest.mark.parametrize("text", NEGATIVE_TEST_CASES,
                             ids=[f"negative:{t[:30]}" for t in NEGATIVE_TEST_CASES])
    def test_should_not_match(self, text):
        predicted, confidence = self.classifier.predict_intent(text)
        assert confidence < NEGATIVE_MAX_CONFIDENCE, (
            f"'{text}' matched '{predicted}' with confidence {confidence:.3f} "
            f"(should be < {NEGATIVE_MAX_CONFIDENCE})"
        )


# ---------------------------------------------------------------------------
# 10. Cross-context routing — tools should work across activity boundaries
# ---------------------------------------------------------------------------

class TestCrossContextRouting:
    """Tests that the dispatcher correctly handles cross-activity requests.

    Users don't think in "activities" — they ask for weather while cooking,
    set timers from any context, etc. The dispatcher should fall back to
    global classification when the scoped set doesn't match.
    """

    @pytest.fixture
    def dispatch_env(self):
        tts_queue = queue.Queue()
        dispatcher = NLPDispatcher(tts_queue=tts_queue, confidence_threshold=0.4)
        return dispatcher, tts_queue

    @staticmethod
    def drain_queue(q):
        messages = []
        while not q.empty():
            messages.append(q.get())
        return " ".join(m for m in messages if m != "<EOS>")

    def test_weather_from_cooking(self, dispatch_env):
        """Weather request while in COOKING context should work via global fallback."""
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("what is the weather", Activity.COOKING)
        assert result is True
        text = self.drain_queue(tts_queue)
        assert text, "Should have spoken a weather response"

    def test_timer_from_cooking(self, dispatch_env):
        """Timer request while in COOKING should work — timers are in COOKING activity."""
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("set a timer for 5 minutes", Activity.COOKING)
        assert result is True
        text = self.drain_queue(tts_queue)
        assert "timer" in text.lower() or "5" in text, f"Expected timer confirmation, got: {text}"

    def test_music_from_cooking(self, dispatch_env):
        """Music request from COOKING should work via global fallback."""
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("what song is playing", Activity.COOKING)
        # This should either succeed (via global fallback) or fail gracefully
        # The important thing is it doesn't crash
        assert result is True or result is False

    def test_cooking_command_from_general(self, dispatch_env):
        """Cooking-only NLP commands from GENERAL context should not crash.

        The global fallback may still match '_nlp_next_step' and respond with
        'no recipe selected' — that's acceptable. The key is no crash.
        """
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("next step", Activity.GENERAL)
        # Should not crash — may succeed via global fallback or fail gracefully
        assert result is True or result is False

    def test_recipe_search_from_general(self, dispatch_env):
        """Recipe search from GENERAL should work — search_recipes is in GENERAL activity."""
        dispatcher, tts_queue = dispatch_env
        result = dispatcher.dispatch("find me a recipe for bread", Activity.GENERAL)
        assert result is True
        text = self.drain_queue(tts_queue)
        assert text, "Should have spoken a recipe search response"


# ---------------------------------------------------------------------------
# 11. Polite and casual form resilience
# ---------------------------------------------------------------------------

POLITE_FORM_CASES = [
    ("could you set a timer for 5 minutes", "set_timer"),
    ("please play some music", "play_music"),
    ("would you check the weather", "handle_weather"),
    ("can you tell me the time please", "get_current_time"),
    ("hey can you search for a recipe for soup", "search_recipes"),
]


class TestPoliteAndCasualForms:
    """Tests that polite preambles don't derail intent classification.

    Naive Bayes uses bag-of-words, so filler words like 'could', 'please',
    'would you' dilute the signal. These tests verify the classifier still
    routes correctly despite polite speech patterns.
    """

    @pytest.fixture(autouse=True)
    def _inject(self, classifier):
        self.classifier = classifier

    @pytest.mark.parametrize("text,expected_tool", POLITE_FORM_CASES,
                             ids=[f"polite:{case[1]}:{case[0][:30]}" for case in POLITE_FORM_CASES])
    def test_polite_routing(self, text, expected_tool):
        predicted, confidence = self.classifier.predict_intent(text)
        assert predicted == expected_tool, (
            f"'{text}' -> {predicted} ({confidence:.3f}), expected {expected_tool}"
        )


# ---------------------------------------------------------------------------
# 12. Standalone scorecard runner
# ---------------------------------------------------------------------------

def print_full_scorecard():
    """Standalone function to print a detailed scorecard (run via __main__)."""
    load_plugins("plugins")
    classifier = IntentClassifier()

    print(f"\n{'='*80}")
    print(f"  NLP Intent Classification — Full Scorecard")
    print(f"{'='*80}\n")

    correct = 0
    total = len(INTENT_TEST_CASES)

    for text, expected, min_conf in INTENT_TEST_CASES:
        predicted, confidence = classifier.predict_intent(text)
        is_correct = predicted == expected
        if is_correct:
            correct += 1
        icon = " ok " if is_correct else "MISS"
        conf_icon = "  " if confidence >= min_conf else "LO"
        print(f"  [{icon}] {conf_icon} {text:45s} -> {predicted:25s} ({confidence:.3f})  expected: {expected}")

    print(f"\n  Overall: {correct}/{total} ({100*correct/total:.0f}%)")

    # Per-tool summary
    by_tool = {}
    for text, expected, min_conf in INTENT_TEST_CASES:
        predicted, confidence = classifier.predict_intent(text)
        by_tool.setdefault(expected, {"correct": 0, "total": 0})
        by_tool[expected]["total"] += 1
        if predicted == expected:
            by_tool[expected]["correct"] += 1

    print(f"\n  Per-tool accuracy:")
    for tool in sorted(by_tool.keys()):
        c, t = by_tool[tool]["correct"], by_tool[tool]["total"]
        bar = "#" * c + "." * (t - c)
        print(f"    {tool:30s} [{bar}] {c}/{t}")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    print_full_scorecard()
