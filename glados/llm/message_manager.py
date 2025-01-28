import threading

from langchain_core.messages import ToolMessage
from loguru import logger

from glados.context.activity import Activity
from glados.llm.client_type import ClientType


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

    def add_message(self, role, content, name=None, images=None, activity: Activity = Activity.GENERAL, architecture=ClientType.OPENAI):
        with self._lock:
            logger.info(f"Add Message: role:{role}, content:{content}, name:{name}, images:{images}")
            if architecture is ClientType.OPENAI:
                message = {"role": role, "content": str(content)}
                if images:
                    message["images"] = images
                if name:
                    message["name"] = name
                self._messages[activity].append(message)
                logger.debug(f"Added message {message}")
            elif architecture is ClientType.LANGCHAIN:
                if role == "tool":
                    self._messages[activity].append(ToolMessage(content=str(content), ))
            else:
                logger.error(f"Unkown architecture: {architecture}")

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
