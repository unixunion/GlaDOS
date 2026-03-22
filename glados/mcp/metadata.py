from typing import Callable, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity


class ToolMetadata:
    """GlaDOS-specific metadata for a tool that MCP doesn't natively support."""

    def __init__(
        self,
        name: str,
        intents: Optional[List[str]] = None,
        activity: Optional[List[Activity]] = None,
        process_output: bool = True,
        callable_check: Optional[Callable] = None,
        handler: Optional[Callable] = None,
    ):
        self.name = name
        self.intents = intents or []
        self.activity = activity or [Activity.GENERAL]
        self.process_output = process_output
        self.callable_check = callable_check
        self.handler = handler


class ToolMetadataRegistry:
    """Registry for GlaDOS-specific tool metadata that lives outside MCP.

    MCP tools only carry name, description, and inputSchema.
    This registry stores the additional metadata GlaDOS needs:
    intents (for IntentClassifier), activity contexts (for tool filtering),
    process_output (for result routing), and the callable handler.
    """

    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._tools: Dict[str, ToolMetadata] = {}
        self._initialized = True

    def register(
        self,
        name: str,
        handler: Callable,
        intents: Optional[List[str]] = None,
        activity: Optional[List[Activity]] = None,
        process_output: bool = True,
        callable_check: Optional[Callable] = None,
    ):
        """Register GlaDOS-specific metadata for a tool."""
        self._tools[name] = ToolMetadata(
            name=name,
            intents=intents,
            activity=activity,
            process_output=process_output,
            callable_check=callable_check,
            handler=handler,
        )
        logger.debug(f"ToolMetadataRegistry: registered metadata for '{name}'")

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable]:
        meta = self._tools.get(name)
        return meta.handler if meta else None

    def get_process_output(self, name: str) -> bool:
        meta = self._tools.get(name)
        return meta.process_output if meta else False

    def get_tools_for_activity(self, activity: Optional[Activity] = None) -> List[str]:
        """Return tool names that match the given activity context.
        If activity is None, returns all tools.
        SYSTEM tools are always included in every context."""
        if activity is None:
            return list(self._tools.keys())
        return [
            name for name, meta in self._tools.items()
            if activity in meta.activity or Activity.SYSTEM in meta.activity
        ]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def all_tools(self) -> Dict[str, ToolMetadata]:
        return dict(self._tools)
