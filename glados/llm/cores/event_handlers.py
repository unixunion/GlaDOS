"""Event handlers for the chat system.

Handles tool events, vision responses, TTS speak events, and tick events.
Extracted from ChatClient to keep it focused on orchestration.
"""

import random
import string

from loguru import logger

from glados.system.event_system import EventSystem, EventHook, EventMessage
from glados.system.plugin import PluginSystem

event_system = EventSystem()
plugin_manager = PluginSystem()


class ChatEventHandlers:
    """Manages event subscriptions and handlers for the chat system.

    Requires access to ChatClient's message_manager, llm_queue, tts_queue.
    """

    def __init__(self, chat_client):
        self._cc = chat_client  # Reference to ChatClient for message_manager, queues, etc.

    def setup(self):
        """Subscribe to all relevant events."""
        event_system.subscribe(
            "tool.*",
            EventHook(name="tool_event_handler", callback=self.handle_tool_event, priority=1)
        )
        event_system.subscribe(
            "system.tick",
            EventHook(name="tick_handler", callback=self.handle_tick, priority=1)
        )
        event_system.subscribe(
            "tts.speak",
            EventHook(name="tts_speak_handler", callback=self.handle_tts_speak, priority=5)
        )
        event_system.subscribe(
            "vision.response",
            EventHook(name="vision_response_handler", callback=self.handle_vision_response)
        )
        logger.debug("Chat event subscriptions set up.")

    def handle_vision_response(self, event: EventMessage):
        """Handle vision responses."""
        description = event.content.get("description", "No description provided.")
        inferred_activity = self._cc.infer_activity_from_input(description)
        self._cc.message_manager.switch_context(inferred_activity)
        additional_prompt = event.content.get("prompt",
            "The following image description comes from a camera surveillance image classifier, "
            "examine the image for any hazards and respond accordingly")
        logger.info(f"Received vision response: {description}")
        response = f"{additional_prompt}, vision model description of image: {description}"
        self._cc.message_manager.add_message_to_current_context("tool", response)
        self._cc.chat(additional_prompt,
                      tools=plugin_manager.get_available_tools(architecture=self._cc.client_type))

    def handle_tool_event(self, event: EventMessage):
        """Handle events from tools."""
        if event.process_output:
            if event.name == "display_chat_input":
                self._cc._from_display = True
                logger.info(f"Processing tool event via LLM: {str(event.content)[:128]}")
                self._cc.llm_queue.put(str(event.content))
            elif event.name not in ("plugin_system",):
                logger.info(f"Processing async tool event: {event.name} — {str(event.content)[:128]}")
                event_system.publish(EventMessage("chat", "async_event", {
                    "role": "async_event",
                    "source": event.name,
                    "content": str(event.content)[:200],
                }))
                event_text = str(event.content)
                if isinstance(event.content, dict) and "message" in event.content:
                    event_text = event.content["message"]
                call_id = ''.join(random.choices(string.ascii_letters + string.digits, k=9))
                self._cc.message_manager.add_message_to_current_context(
                    "assistant", None, tool_calls=[{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": event.name, "arguments": "{}"}
                    }]
                )
                self._cc.message_manager.add_message_to_current_context(
                    "tool", event_text, name=event.name, tool_call_id=call_id
                )
                self._cc.chat(None, tools=None, _recursive=True, _depth=1)
            else:
                logger.info(f"Processing tool event via LLM: {str(event.content)[:128]}")
                self._cc.llm_queue.put(str(event.content))
        else:
            content_str = str(event.content)
            # Deduplicate display_state messages — replace previous instead of accumulating
            if "<display_state>" in content_str:
                self._cc.message_manager.replace_last_display_state(content_str)
            else:
                logger.debug(f"Adding tool event to history as system message: {content_str[:128]}")
                self._cc.message_manager.add_message("system", event.content)

    def handle_tts_speak(self, event: EventMessage):
        """Handle tts.speak events — send text directly to TTS without LLM processing."""
        text = str(event.content).strip()
        if text:
            logger.info(f"[TTS] Direct speak: {text[:100]}")
            self._cc.tts_queue.put(text)
            self._cc.tts_queue.put("<EOS>")

    def handle_tick(self, event: EventMessage):
        logger.debug(f"Received tick: {event}")
