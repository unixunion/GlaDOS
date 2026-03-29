"""NLP dispatcher — classifies intent, extracts params, calls tools, speaks results."""

import queue
import re
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.nlp.handler import NLPHandlerRegistry
from glados.system.event_system import EventSystem, EventMessage
from glados.system.intent_classifier import IntentClassifier
from glados.system.plugin import PluginSystem


@dataclass
class HybridResult:
    """Result of a hybrid NLP fast-path dispatch attempt."""
    handled: bool = False              # True = fully handled, caller should return
    tool_result: Any = None            # If set, inject into history and let LLM summarize
    tool_name: Optional[str] = None    # Name of executed tool


class NLPDispatcher:
    """Orchestrates NLP-mode tool dispatch without an LLM.

    Flow: classify intent -> extract params -> call tool -> format response -> TTS

    Uses two-layer classification:
      1. Activity-scoped: classify against tools available in the current activity
      2. Global fallback: if scoped classification fails, try all tools
    """

    def __init__(self, tts_queue: queue.Queue, confidence_threshold: float = 0.4):
        self._tts_queue = tts_queue
        self._confidence_threshold = confidence_threshold
        self._classifier = IntentClassifier()
        self._registry = NLPHandlerRegistry()
        self._plugin_system = PluginSystem()
        self._last_result = None  # For multi-step flows (e.g. recipe search -> select)
        self._session: dict[Activity, dict] = {}  # Per-activity session data
        self._registry.dispatcher = self

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize symbols to words so the bag-of-words classifier can match them."""
        text = re.sub(r'\+', ' plus ', text)
        text = re.sub(r'\-(?=\s*\d)', ' minus ', text)
        text = re.sub(r'\*|×', ' times ', text)
        text = re.sub(r'/|÷', ' divided by ', text)
        text = re.sub(r'°\s*[Ff]', ' fahrenheit', text)
        text = re.sub(r'°\s*[Cc]', ' celsius', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def get_session(self, activity: Activity) -> dict:
        """Get session data for an activity. Returns empty dict if none."""
        return self._session.get(activity, {})

    def try_hybrid_dispatch(self, text: str, activity: Activity, threshold: float) -> HybridResult:
        """Attempt hybrid NLP fast-path — high-confidence tool execution that bypasses the LLM.

        Uses two-layer classification (scoped, then global). If confidence meets
        the threshold, either dispatches via NLP handler or executes the tool
        directly for LLM summarization.

        Args:
            text: User input text.
            activity: Current activity context.
            threshold: Minimum confidence to trigger fast-path.

        Returns:
            HybridResult with handled=True if fully dispatched, or tool_result set
            if the caller should inject the result and let the LLM summarize.
        """
        if not self._classifier.model:
            return HybridResult()

        event_system = EventSystem()

        # Try scoped classification first, then global fallback
        scoped_tools = self._get_tool_names_for_activity(activity)
        predicted, confidence = "", 0.0
        if scoped_tools:
            predicted, confidence = self._classifier.predict_intent_scoped(text, scoped_tools)
        if not predicted or confidence < threshold:
            predicted, confidence = self._classifier.predict_intent(text)

        # Per-tool threshold overrides global threshold
        effective_threshold = self._plugin_system.get_nlp_threshold(predicted, default=threshold)

        if not predicted or confidence < effective_threshold or predicted.startswith("_memory"):
            return HybridResult()

        logger.info(f"[Hybrid] NLP fast-path: '{predicted}' confidence {confidence:.2f} >= {effective_threshold:.2f}"
                     f"{' (per-tool)' if effective_threshold != threshold else ''}")

        has_nlp_handler = self._registry.has_handler(predicted)
        needs_llm = self._plugin_system.should_process_plugin_output(predicted)

        if has_nlp_handler:
            # NLP handler exists — dispatch directly (fast, no LLM)
            event_system.publish(EventMessage("chat", "tool_call", {
                "role": "tool_call",
                "tool": predicted,
                "args": {},
                "via": "nlp",
            }))
            dispatched = self.dispatch(text, activity)
            if dispatched:
                logger.info(f"[Hybrid] Completed via NLP dispatch (skipped LLM)")
                return HybridResult(handled=True)
            logger.info("[Hybrid] NLP dispatch returned False, falling through to LLM")
            return HybridResult()

        if needs_llm:
            # No NLP handler but tool needs LLM summarization —
            # execute tool directly, return result for caller to inject
            func = self._plugin_system.get_available_llm_functions().get(predicted)
            if func:
                try:
                    result = func()
                    tool_result = {"tool": predicted, "result": result}
                    logger.info(f"[Hybrid] Executed '{predicted}' via direct call, handing to LLM for summary")
                    return HybridResult(tool_result=tool_result, tool_name=predicted)
                except Exception as e:
                    logger.warning(f"[Hybrid] Direct execution failed for '{predicted}': {e}, falling through to LLM")

        return HybridResult()

    def dispatch(self, text: str, activity: Activity) -> bool:
        """Classify and dispatch user input to the appropriate tool.

        Uses two-layer classification:
          Layer 1: Scoped to current activity's tools — if confident, use it.
          Layer 2: Global fallback — if scoped fails, try all tools.

        Returns True if a tool was called, False if nothing matched.
        """
        if not self._classifier.model:
            logger.warning("[NLP] IntentClassifier has no trained model")
            self._speak("I'm not ready yet. No tools have been loaded.")
            return False

        # Normalize symbols to words for better bag-of-words matching
        text = self._normalize_text(text)

        # Scoped classification uses a lower threshold — the tool list is already
        # filtered to the current activity, so false positives are less likely.
        scoped_threshold = self._confidence_threshold * 0.5

        # --- Layer 1: Activity-scoped classification ---
        scoped_tools = self._get_tool_names_for_activity(activity)
        predicted, confidence = "", 0.0

        if scoped_tools:
            predicted, confidence = self._classifier.predict_intent_scoped(text, scoped_tools)
            logger.info(f"[NLP] Scoped intent ({activity.name}): {predicted} "
                        f"(confidence={confidence:.2f}, threshold={scoped_threshold:.2f})")

        # Skip memory intents — handled by ChatClient's existing memory code
        if predicted and predicted.startswith("_memory_"):
            logger.debug(f"[NLP] Skipping memory intent {predicted} — handled upstream")
            return False

        # --- Layer 2: Global fallback ---
        if confidence < scoped_threshold or not predicted:
            predicted, confidence = self._classifier.predict_intent(text)
            logger.info(f"[NLP] Global fallback: {predicted} "
                        f"(confidence={confidence:.2f}, threshold={self._confidence_threshold})")

            if predicted and predicted.startswith("_memory_"):
                logger.debug(f"[NLP] Skipping memory intent {predicted} — handled upstream")
                return False

        if confidence < self._confidence_threshold or not predicted:
            self._speak("I didn't understand that. Could you try rephrasing?")
            return True

        # --- Execute the matched tool ---
        return self._execute_tool(predicted, text)

    def _get_tool_names_for_activity(self, activity: Activity) -> list[str]:
        """Get tool names available for the given activity.

        Combines plugin system tools (activity-filtered) with NLP-only handlers
        registered for this activity.
        """
        # Tool names from the plugin system (activity-scoped)
        tool_names = []
        for name, plugin_data in self._plugin_system.plugins.items():
            plugin_activities = plugin_data.get("activity", [Activity.GENERAL])
            if activity in plugin_activities or Activity.SYSTEM in plugin_activities:
                tool_names.append(name)

        # Add NLP-only handlers scoped to this activity (e.g. cooking context commands)
        nlp_tools = self._registry.get_for_activity(activity)
        for name in nlp_tools:
            if name not in tool_names:
                tool_names.append(name)

        return tool_names

    def _execute_tool(self, predicted: str, text: str) -> bool:
        """Look up and execute the predicted tool, then speak the result."""
        # Check for NLP-only handler first (not in plugin system)
        handler = self._registry.get(predicted)

        # Try plugin system
        plugin_data = self._plugin_system.plugins.get(predicted)
        if plugin_data:
            func = plugin_data.get("function")
        elif handler and handler.extract_fn:
            # NLP-only handler with its own extract_fn — it handles everything
            func = None
        else:
            func = None

        if not plugin_data and not (handler and handler.response_fn):
            logger.warning(f"[NLP] Tool '{predicted}' not found in plugin system or NLP handlers")
            self._speak(f"I recognized {predicted} but couldn't find the tool.")
            return True

        if plugin_data and not callable(plugin_data.get("function")):
            logger.warning(f"[NLP] Tool '{predicted}' function is not callable")
            self._speak("I found the tool but it's not available right now.")
            return True

        try:
            if handler:
                kwargs = handler.extract_params(text)
                logger.info(f"[NLP] Calling {predicted} with params: {kwargs}")
                if func:
                    result = func(**kwargs)
                else:
                    # NLP-only handler — pass dispatcher session as context
                    result = kwargs  # Handler uses session data directly
                response = handler.format_response(result)
            else:
                # No handler — try parameterless call
                logger.info(f"[NLP] Calling {predicted} with no params (no handler registered)")
                try:
                    result = func()
                    response = self._default_format(result)
                except TypeError as e:
                    logger.warning(f"[NLP] Parameterless call failed for {predicted}: {e}")
                    self._speak(f"I recognized {predicted} but couldn't extract the details needed.")
                    return True

            self._last_result = result
            logger.info(f"[NLP] Tool result: {str(result)[:200]}")

            # Store session data for select_recipe
            if predicted == "select_recipe" and isinstance(result, dict) and result.get("status") == "success":
                self._session[Activity.COOKING] = {
                    "selected_recipe": result,
                    "current_step": 0,
                }
                logger.info(f"[NLP] Stored cooking session: {result.get('title')}")

            self._speak(response)
            return True

        except Exception as e:
            logger.exception(f"[NLP] Error calling {predicted}: {e}")
            self._speak(f"Something went wrong while running {predicted}.")
            return True

    def _speak(self, text: str):
        """Send text to the TTS queue."""
        if text:
            self._tts_queue.put(text)
            self._tts_queue.put("<EOS>")

    @staticmethod
    def _default_format(result) -> str:
        """Best-effort formatting when no NLPHandler is registered."""
        if result is None:
            return "Done."
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if "error" in result:
                return f"Sorry, there was an error: {result['error']}"
            if "message" in result:
                return str(result["message"])
            if "result" in result:
                return str(result["result"])
            # Try to produce something readable
            parts = []
            for k, v in result.items():
                if k.startswith("_"):
                    continue
                parts.append(f"{k}: {v}")
            return ". ".join(parts) if parts else "Done."
        if isinstance(result, list):
            if not result:
                return "No results found."
            return ". ".join(str(item) for item in result[:5])
        return str(result)
