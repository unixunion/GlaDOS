import threading

from loguru import logger

from glados.context.activity import Activity


class MessageManager:

    def __init__(self):
        self._messages = {
            Activity.GENERAL: [],
            Activity.CHORES: [],
            Activity.UTILITIES: [],
            Activity.COOKING: [],
            Activity.ENTERTAINMENT: [],
            Activity.SYSTEM: []
        }
        self.current_context = Activity.GENERAL
        self._lock = threading.Lock()

    def add_message(self, role, content, name=None, images=None, activity: Activity = Activity.GENERAL):
        with self._lock:
            message = {"role": role, "content": content}
            # message = {"role": role, "content": str(content).strip()}
            if images:
                message["images"] = images
            if name:
                message["name"] = name
            self._messages[activity].append(message)
            logger.debug(f"Added message {message}")

    def add_message_to_current_context(self, role, content, name=None, images=None):
        """Helper to add messages to the current context."""
        self.add_message(role, content, name=name, images=images, activity=self.current_context)

    def get_messages(self):
        with self._lock:
            return self._messages[self.current_context].copy()

    def switch_context(self, activity: Activity):
        """Switch the current context to a specific activity."""
        with self._lock:
            if activity in self._messages:
                logger.info(f"Switching context to: {activity}")
                self.current_context = activity
            else:
                logger.warning(f"Activity {activity} is not a recognized context!")
