"""NLP handler registry — maps tool names to parameter extractors and response formatters."""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from glados.context.activity import Activity


@dataclass
class NLPHandler:
    """Defines how to extract parameters and format responses for a tool in NLP mode."""

    tool_name: str
    extractors: dict[str, list[re.Pattern]] = field(default_factory=dict)
    response_fn: Optional[Callable[[Any], str]] = None
    extract_fn: Optional[Callable[[str], dict]] = None
    activity: Optional[list[Activity]] = None

    def extract_params(self, text: str) -> dict:
        """Extract tool parameters from user text.

        If extract_fn is set, delegates to it. Otherwise uses regex extractors.
        Each key in self.extractors maps to a parameter name. The patterns should
        contain a named group matching the parameter name (or group 1 as fallback).
        First matching pattern wins for each parameter.
        """
        if self.extract_fn:
            return self.extract_fn(text)

        params = {}
        for param_name, patterns in self.extractors.items():
            for pattern in patterns:
                m = pattern.search(text)
                if m:
                    try:
                        value = m.group(param_name)
                    except (IndexError, re.error):
                        try:
                            value = m.group(1)
                        except (IndexError, re.error):
                            continue
                    if value:
                        params[param_name] = value.strip()
                        break
        return params

    def format_response(self, result: Any) -> str:
        """Format a tool result as spoken text."""
        if self.response_fn:
            try:
                return self.response_fn(result)
            except Exception as e:
                logger.warning(f"[NLP] Response formatter failed for {self.tool_name}: {e}")
                return str(result)
        # Default: stringify the result
        if isinstance(result, dict):
            if "error" in result:
                return f"Sorry, there was an error: {result['error']}"
            if "message" in result:
                return str(result["message"])
            if "result" in result:
                return str(result["result"])
        return str(result)


class NLPHandlerRegistry:
    """Singleton registry mapping tool names to NLPHandler instances."""

    _instance = None
    _handlers: dict[str, NLPHandler]

    def __new__(cls):
        if not cls._instance:
            inst = super().__new__(cls)
            inst._handlers = {}
            inst._dispatcher = None
            cls._instance = inst
        return cls._instance

    @property
    def dispatcher(self):
        """The active NLPDispatcher instance (set by the dispatcher on construction)."""
        return self._dispatcher

    @dispatcher.setter
    def dispatcher(self, value):
        self._dispatcher = value

    def register(self, handler: NLPHandler):
        """Register an NLP handler for a tool."""
        self._handlers[handler.tool_name] = handler
        logger.debug(f"[NLP] Registered handler for tool: {handler.tool_name}")

    def get(self, tool_name: str) -> Optional[NLPHandler]:
        """Get the NLP handler for a tool, or None."""
        return self._handlers.get(tool_name)

    def has_handler(self, tool_name: str) -> bool:
        return tool_name in self._handlers

    def get_for_activity(self, activity: Activity) -> list[str]:
        """Return tool names whose NLPHandler is scoped to the given activity.

        Handlers with activity=None are considered global (match any activity).
        """
        result = []
        for name, handler in self._handlers.items():
            if handler.activity is None or activity in handler.activity:
                result.append(name)
        return result
