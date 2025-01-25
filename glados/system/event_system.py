import fnmatch
import json
import threading
import time
from queue import Queue, Empty
from typing import Callable, List, Dict, Union, Any

from loguru import logger
from dataclasses import dataclass, field


@dataclass
class EventMessage:
    """Event Message Class"""
    role: str = "tool"  # Role in the conversation context
    name: str = "event"  # Name of the tool or event
    content: Union[str, Dict[str, Any]] = field(default_factory=dict)  # Main payload of the message
    process_output: bool = False  # Determines if the LLM processes the event

    def to_dict(self) -> Dict[str, Any]:
        """Convert the message to a dictionary format."""
        return {
            "role": self.role,
            "name": self.name,
            "content": self.content,
            "process_output": self.process_output
        }

    def to_json(self) -> str:
        """Convert the message to a JSON string."""
        return json.dumps(self.to_dict())


# Event Hook Class
class EventHook:
    def __init__(self, name: str, callback: Callable[[EventMessage], None], priority: int = 0):
        self.name = name
        self.callback = callback
        self.priority = priority

    def trigger(self, event: EventMessage) -> None:
        try:
            logger.debug(f"Triggering hook '{self.name}' with event: {str(event)}")
            self.callback(event)
        except Exception as e:
            logger.error(f"Error in hook '{self.name}': {e}")


# EventSystem with Pub-Sub Model
class EventSystem:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            logger.info("Instantiating singleton")
            cls._instance = super(EventSystem, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._subscribers_lock = threading.Lock()
        self._initialized = True  # Mark as initialized
        self._subscribers: Dict[str, List[EventHook]] = {}
        self._event_queue: Queue[EventMessage] = Queue()
        self._stop_event = threading.Event()
        self._ticker_thread = None
        self.start_ticker(interval=1.0)

    def subscribe(self, topic: str, hook: EventHook):
        """Subscribe a hook to a specific topic or pattern.

        Args:
            topic (str): topic in format role.name, e.g "system.tick", "tool.*"
        """
        with self._subscribers_lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(hook)
            self._subscribers[topic].sort(key=lambda h: h.priority, reverse=True)
            logger.info(f"Subscribed hook '{hook.name}' to topic '{topic}'.")
            logger.info(f"All subscriptions: {self._subscribers}")

    def unsubscribe(self, topic: str, hook_name: str):
        """Unsubscribe a hook by name."""
        with self._subscribers_lock:
            if topic in self._subscribers:
                self._subscribers[topic] = [
                    hook for hook in self._subscribers[topic] if hook.name != hook_name
                ]
                logger.info(f"Unsubscribed hook '{hook_name}' from topic '{topic}'.")

    def publish(self, event: EventMessage):
        """Publish an event to all subscribers."""
        if self._make_topic(event) != "system.tick":
            logger.info(f"Enqueue event: {str(event)[0:128]}")
        self._event_queue.put(event)

    @staticmethod
    def _make_topic(event: EventMessage):
        return f"{event.role}.{event.name}"

    def _dispatch_events(self):
        """Dispatch events from the queue to subscribers."""
        if len(self._subscribers) == 0:
            logger.info("No subscribers, bailing out")
            time.sleep(10)
        logger.debug("Dispatching events")
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
                topic = self._make_topic(event)
                logger.debug(f"Dispatching event: {event} with topic '{topic}'")

                # Lock the subscribers dictionary during iteration
                with self._subscribers_lock:
                    for subscription, hooks in list(self._subscribers.items()):  # Use list() to prevent runtime issues
                        logger.debug(f"Matching {topic} against {subscription}")
                        if fnmatch.fnmatch(topic, subscription):
                            logger.debug(f"Topic '{topic}' matches subscription '{subscription}'")
                            for hook in hooks:
                                if event.name != 'tick':
                                    logger.info(f"Sending event: {str(event)[0:128]}... to {hook.name}")
                                hook.trigger(event)
                        else:
                            logger.debug(f"Topic '{topic}' does not match subscription '{subscription}'")
            except Empty:
                break

    def start_ticker(self, interval: float = 1.0):
        logger.info("Starting ticker")
        """Start a thread to periodically dispatch events."""
        if self._ticker_thread and self._ticker_thread.is_alive():
            logger.warning("Ticker already running, bailing out")
            return  # Ticker is already running

        def ticker():
            logger.info("Starting Ticker")
            while not self._stop_event.is_set():
                tick_event = EventMessage(
                    role="system",
                    name="tick",
                    content={"time": time.time()},
                    process_output=False,
                )
                self.publish(tick_event)
                self._dispatch_events()
                time.sleep(interval)

        logger.info("Startnig the ticker thread")
        self._stop_event.clear()
        self._ticker_thread = threading.Thread(target=ticker, daemon=True)
        self._ticker_thread.start()
        logger.info("Ticker thread started.")

    def stop_ticker(self):
        """Stop the ticker thread."""
        self._stop_event.set()
        if self._ticker_thread:
            self._ticker_thread.join()
        logger.info("Ticker thread stopped.")


# Example Usage
if __name__ == "__main__":
    def tick_hook(event: EventMessage):
        logger.info(f"Tick Hook: {event.content['time']}")


    def all_system_events(event: EventMessage):
        logger.info(f"System Event Hook Triggered: {event.to_dict()}")


    def specific_tool_event(event: EventMessage):
        logger.info(f"Specific Tool Event Hook Triggered: {event.to_dict()}")


    event_system = EventSystem()

    # Subscribe to specific events
    event_system.subscribe("system.tick", EventHook(name="tick_hook", callback=tick_hook, priority=2))
    event_system.subscribe("system.*", EventHook(name="system_events", callback=all_system_events, priority=1))
    event_system.subscribe("tool.x", EventHook(name="tool_x_event", callback=specific_tool_event, priority=1))

    # Publish events
    event_system.publish(EventMessage(role="system", name="tick", content={"time": time.time()}))
    event_system.publish(EventMessage(role="system", name="update", content={"status": "complete"}))
    event_system.publish(EventMessage(role="tool", name="x", content={"message": "Hello from Tool X"}))

    # Start the ticker
    event_system.start_ticker(interval=1.0)

    try:
        time.sleep(5)  # Let the ticker run for 5 seconds
    finally:
        event_system.stop_ticker()
