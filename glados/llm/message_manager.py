import threading

from langchain_core.messages import ToolMessage
from loguru import logger

from glados.context.activity import Activity
from glados.llm.client_type import ClientType


class MessageManager:

    def __init__(self, max_context_messages: int = 20):
        self._messages = {
            Activity.GENERAL: [],
            Activity.CHORES: [],
            Activity.UTILITIES: [],
            Activity.COOKING: [],
            Activity.ENTERTAINMENT: [],
            Activity.SYSTEM: []
        }
        self.current_context = Activity.GENERAL
        self.max_context_messages = max_context_messages
        self._lock = threading.Lock()

    def add_message(self, role, content, name=None, images=None, activity: Activity = Activity.GENERAL, architecture=ClientType.OPENAI):
        with self._lock:
            if role == "system":
                logger.debug(f"Add Message: role:{role}, content:{str(content)[:80]}..., activity:{activity}")
            else:
                logger.info(f"Add Message: role:{role}, content:{str(content)[:128]}, name:{name}")
            if architecture is ClientType.OPENAI:
                message = {"role": role, "content": str(content)}
                if images:
                    message["images"] = images
                if name:
                    message["name"] = name
                self._messages[activity].append(message)
                self._trim_context(activity)
                logger.debug(f"Added message {message}")
            elif architecture is ClientType.LANGCHAIN:
                if role == "tool":
                    self._messages[activity].append(ToolMessage(content=str(content), ))
                    self._trim_context(activity)
            else:
                logger.error(f"Unknown architecture: {architecture}")

    def _trim_context(self, activity: Activity):
        """Trim the message list to max_context_messages, preserving the system prompt."""
        msgs = self._messages[activity]
        if len(msgs) <= self.max_context_messages:
            return

        # Preserve system messages at the start (personality prompt, etc.)
        system_prefix = []
        rest = []
        for msg in msgs:
            if not rest and isinstance(msg, dict) and msg.get("role") == "system":
                system_prefix.append(msg)
            else:
                rest.append(msg)

        # Keep system prefix + the most recent messages that fit
        max_rest = self.max_context_messages - len(system_prefix)
        if max_rest < 1:
            max_rest = 1
        trimmed = len(rest) - max_rest
        if trimmed > 0:
            logger.info(f"Trimming {trimmed} old message(s) from {activity.name} context "
                        f"({len(msgs)} -> {len(system_prefix) + max_rest})")
            self._messages[activity] = system_prefix + rest[-max_rest:]

    def add_message_to_current_context(self, role, content, name=None, images=None):
        """Helper to add messages to the current context."""
        self.add_message(role, content, name=name, images=images, activity=self.current_context)

    def get_messages(self):
        with self._lock:
            msgs = self._messages[self.current_context].copy()

            # Ensure tool messages don't appear before the first user message.
            # Some model templates (e.g., Qwen) require a user message first.
            # Drop any tool messages that arrive before the first user message —
            # these are typically just startup notifications (e.g., "loaded 148 recipes").
            first_user_idx = None
            for i, msg in enumerate(msgs):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    first_user_idx = i
                    break

            if first_user_idx is not None and first_user_idx > 0:
                filtered = [
                    msg for i, msg in enumerate(msgs)
                    if not (i < first_user_idx and isinstance(msg, dict) and msg.get("role") == "tool")
                ]
                if len(filtered) < len(msgs):
                    logger.debug(f"Dropped {len(msgs) - len(filtered)} tool message(s) before first user message")
                    msgs = filtered

            return msgs

    def switch_context(self, activity: Activity):
        """Switch the current context to a specific activity."""
        with self._lock:
            if activity in self._messages:
                logger.info(f"Switching context to: {activity}")
                self.current_context = activity
            else:
                logger.warning(f"Activity {activity} is not a recognized context!")
