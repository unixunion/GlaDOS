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
        # Full conversation history — never trimmed, used for log reports and debugging.
        # Separate from _messages which is trimmed for the LLM context window.
        from collections import deque
        self._history = deque(maxlen=200)  # Last 200 messages across all contexts

    def add_message(self, role, content, name=None, images=None, tool_call_id=None, tool_calls=None, activity: Activity = Activity.GENERAL, architecture=ClientType.OPENAI):
        with self._lock:
            if role == "system":
                logger.debug(f"Add Message: role:{role}, content:{str(content)[:80]}..., activity:{activity}")
            else:
                logger.info(f"Add Message: role:{role}, content:{str(content)[:128]}, name:{name}")
            if architecture is ClientType.OPENAI:
                message = {"role": role, "content": str(content) if content is not None else None}
                if images:
                    message["images"] = images
                if name:
                    message["name"] = name
                if tool_call_id:
                    message["tool_call_id"] = tool_call_id
                if tool_calls:
                    message["tool_calls"] = tool_calls
                self._messages[activity].append(message)
                self._trim_context(activity)
                # Also append to full history (never trimmed — for log reports)
                from datetime import datetime
                self._history.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "activity": activity.name,
                    "role": role,
                    "content": str(content)[:500] if content else None,
                    "name": name,
                    "tool_calls": [
                        {"name": tc.get("function", {}).get("name", "?"),
                         "args": str(tc.get("function", {}).get("arguments", ""))[:200]}
                        for tc in (tool_calls or [])
                    ] if tool_calls else None,
                })
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
        if len(rest) > max_rest:
            # Find a safe trim point — don't leave orphaned tool_calls or tool results
            # Walk backward from the trim boundary to find a user message (safe boundary)
            trim_target = len(rest) - max_rest
            safe_trim = trim_target
            for i in range(trim_target, len(rest)):
                msg = rest[i]
                if isinstance(msg, dict) and msg.get("role") == "user":
                    safe_trim = i
                    break
                # Skip past orphaned assistant+tool_calls and tool results
                safe_trim = i + 1

            trimmed = safe_trim
            if trimmed > 0:
                logger.info(f"Trimming {trimmed} old message(s) from {activity.name} context "
                            f"({len(msgs)} -> {len(system_prefix) + len(rest) - trimmed})")
                self._messages[activity] = system_prefix + rest[trimmed:]

    def replace_last_display_state(self, content: str):
        """Replace the most recent display_state system message instead of appending.

        This prevents context bloat from rapid UI updates (e.g., toggling items
        on the shopping list generates multiple display_state events per second).
        """
        with self._lock:
            msgs = self._messages[self.current_context]
            # Search backward for the last display_state message and replace it
            for i in range(len(msgs) - 1, -1, -1):
                msg = msgs[i]
                if isinstance(msg, dict) and msg.get("role") == "system" and "<display_state>" in str(msg.get("content", "")):
                    msgs[i] = {"role": "system", "content": content}
                    logger.debug(f"Replaced display_state in context (position {i})")
                    return
            # No existing display_state — add as new
            msgs.append({"role": "system", "content": content})
            self._trim_context(self.current_context)
            logger.debug(f"Added first display_state to context")

    def add_message_to_current_context(self, role, content, name=None, images=None, tool_call_id=None, tool_calls=None):
        """Helper to add messages to the current context."""
        self.add_message(role, content, name=name, images=images, tool_call_id=tool_call_id, tool_calls=tool_calls, activity=self.current_context)

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
