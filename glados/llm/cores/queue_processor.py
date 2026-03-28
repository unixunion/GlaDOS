"""LLM queue processor — drains transcription queue and dispatches to chat.

Extracted from ChatClient to separate queue management from chat logic.
"""

import queue
import threading
import time

from loguru import logger

from glados.system.event_system import EventSystem, EventMessage

event_system = EventSystem()


class LLMQueueProcessor:
    """Processes the LLM input queue in a background thread.

    Drains stale messages (user may have repeated while LLM was busy)
    and dispatches the latest message to chat().
    """

    def __init__(self, llm_queue: queue.Queue, chat_fn, shutdown_event: threading.Event):
        self._llm_queue = llm_queue
        self._chat_fn = chat_fn
        self._shutdown_event = shutdown_event
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def _run(self):
        """Monitor the LLM queue and process messages."""
        while not self._shutdown_event.is_set():
            try:
                user_input = self._llm_queue.get(timeout=0.1)
                if user_input:
                    # Drain stale queued messages — keep only the latest
                    latest = user_input
                    drained = 0
                    while not self._llm_queue.empty():
                        try:
                            latest = self._llm_queue.get_nowait()
                            drained += 1
                        except queue.Empty:
                            break
                    if drained > 0:
                        logger.info(f"Drained {drained} stale message(s) from LLM queue, using latest")

                    logger.success(f"Processing input from LLM queue: {latest[:100]}")
                    event_system.publish(EventMessage("status", "thinking", {"message": "Thinking..."}))
                    self._chat_fn(latest, tools=None)
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"Error processing LLM queue: {e}")
            time.sleep(0.1)
