import threading
import time
from typing import Callable, List, Dict, Any
from queue import Queue, Empty

"""
The event system should be usable from functions, so they can send information here perhaps from long running
threads. Such as timers and external notifications. This class will be occasionally polled by the lmclient for 
new messages, likely the format:

{
    "tool_call_id": tool_call.id,
    "role": "tool",
    "name": function_name,
    "content": function_response,
}

which will then be added to the conversation context. 
"""


class EventSystem:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(EventSystem, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._hooks: List[Callable[[Dict[str, Any]], None]] = []
        self._event_queue: Queue[Dict[str, Any]] = Queue()
        self._stop_event = threading.Event()
        self._ticker_thread = None

    def register_hook(self, hook: Callable[[Dict[str, Any]], None]) -> None:
        """Register a function to be called periodically or when events occur."""
        self._hooks.append(hook)

    def add_event(self, event: Dict[str, Any]) -> None:
        """Add a new event to the system for distribution."""
        self._event_queue.put(event)

    def trigger_hooks(self):
        """Call all registered hooks."""
        for hook in self._hooks:
            try:
                hook()
            except Exception as e:
                print(f"Error in hook: {e}")

    def start_ticker(self, interval: float = 1.0):
        """Start a thread that periodically triggers hooks."""
        if self._ticker_thread and self._ticker_thread.is_alive():
            return  # Ticker is already running

        def ticker():
            while not self._stop_event.is_set():
                self.trigger_hooks()
                time.sleep(interval)

        self._stop_event.clear()
        self._ticker_thread = threading.Thread(target=ticker, daemon=True)
        self._ticker_thread.start()

    def stop_ticker(self):
        """Stop the ticker thread."""
        self._stop_event.set()
        if self._ticker_thread:
            self._ticker_thread.join()

    def poll_events(self) -> List[Dict[str, Any]]:
        """Poll all available events from the queue."""
        events = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except Empty:
                break
        return events


# Example Usage
if __name__ == "__main__":
    event_system = EventSystem()

    # Adding an event
    event_system.add_event({
        "tool_call_id": "12345",
        "role": "tool",
        "name": "example_function",
        "content": "Example response"
    })

    # Polling events
    events = event_system.poll_events()
    print(events)  # [{'tool_call_id': '12345', 'role': 'tool', 'name': 'example_function', 'content': 'Example response'}]

    # Verify that events are cleared after polling
    print(event_system.poll_events())  # []
