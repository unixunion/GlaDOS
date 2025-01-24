import threading
from enum import Enum

from loguru import logger


class Context(Enum):
    GENERAL = 1,
    COOKING = 2,
    GAME = 3,
    MUSIC = 4


class MessageManager:

    def __init__(self):
        self._messages = {
            Context.GENERAL: [],
            Context.COOKING: [],
            Context.GAME: [],
            Context.MUSIC: []
        }
        self.current_context = Context.GENERAL
        self._lock = threading.Lock()

    def add_message(self, role, content, name=None, images=None, context: Context = Context.GENERAL):
        with self._lock:
            message = {"role": role, "content": content}
            # message = {"role": role, "content": str(content).strip()}
            if images:
                message["images"] = images
            if name:
                message["name"] = name
            self._messages[context].append(message)
            logger.debug(f"Added message {message}")

    def get_messages(self):
        with self._lock:
            return self._messages[self.current_context].copy()
