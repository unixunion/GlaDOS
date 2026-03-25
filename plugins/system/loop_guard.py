"""Loop Guard — monitors TTS output for repetition and interrupts runaway LLM responses.

Subscribes to status.speaking events, maintains a ring buffer of recent sentences,
and fires system.interrupt_tts + injects a system message when a loop is detected.
"""
from collections import deque

from loguru import logger

from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventSystem, EventMessage, EventHook


class LoopGuard(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        self.event_system = EventSystem()
        buffer_size = self.plugin_config.get("buffer_size", 15)
        self._buffer: deque[str] = deque(maxlen=buffer_size)
        self._loop_count = 0
        self._active = True

    def start(self):
        self.event_system.subscribe(
            "status.speaking",
            EventHook("loop_guard_speaking", callback=self._on_speaking, priority=1)
        )
        self.event_system.subscribe(
            "status.idle",
            EventHook("loop_guard_idle", callback=self._on_idle, priority=1)
        )
        logger.info("[LoopGuard] Monitoring TTS for repetition loops")

    def stop(self):
        self.event_system.unsubscribe("status.speaking", "loop_guard_speaking")
        self.event_system.unsubscribe("status.idle", "loop_guard_idle")

    def _on_speaking(self, event: EventMessage):
        """Called for each sentence sent to TTS."""
        if not self._active:
            return

        message = event.content.get("message", "") if isinstance(event.content, dict) else str(event.content)
        if not message:
            return

        normalized = message.strip().lower()

        if normalized in self._buffer:
            self._loop_count += 1
            if self._loop_count >= 2:
                logger.warning(f"[LoopGuard] Loop detected ({self._loop_count} repeats), interrupting TTS: {message[:60]}...")
                self._active = False  # Stop checking until reset
                # Interrupt TTS — this flushes the queue and stops audio
                self.event_system.publish(EventMessage("system", "interrupt_tts", {}))
        else:
            self._loop_count = 0

        self._buffer.append(normalized)

    def _on_idle(self, event: EventMessage):
        """Reset state when TTS finishes a response."""
        self._buffer.clear()
        self._loop_count = 0
        self._active = True
