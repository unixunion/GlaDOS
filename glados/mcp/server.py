import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from mcp.types import Tool, CallToolResult, TextContent


class GladosMCPServer:
    """In-process MCP server for GlaDOS.

    Stores tools using MCP types (Tool, CallToolResult) for standardization,
    but invokes handlers directly without async transport overhead.
    This keeps GlaDOS's synchronous/threaded architecture intact.

    For external MCP servers (Phase 3), MCPClientBridge will handle
    actual MCP protocol communication via stdio/SSE.
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
        self._tools: Dict[str, Tool] = {}
        self._handlers: Dict[str, Callable] = {}
        self._initialized = True
        logger.info("GladosMCPServer initialized")

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
    ):
        """Register a tool with MCP-standard schema.

        Args:
            name: Tool name (must be unique)
            description: Human-readable description
            input_schema: JSON Schema for the tool's parameters
            handler: The Python function to call when this tool is invoked
        """
        tool = Tool(
            name=name,
            description=description,
            inputSchema=input_schema,
        )
        self._tools[name] = tool
        self._handlers[name] = handler
        logger.debug(f"MCP server: registered tool '{name}'")

    def list_tools(self, tool_names: Optional[List[str]] = None) -> List[Tool]:
        """List available tools, optionally filtered by name.

        Args:
            tool_names: If provided, only return tools with these names.
                       Used by the activity filtering system.
        """
        if tool_names is None:
            return list(self._tools.values())
        return [self._tools[n] for n in tool_names if n in self._tools]

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> CallToolResult:
        """Execute a tool by name and return an MCP CallToolResult.

        This is a direct in-process call — no transport overhead.
        """
        handler = self._handlers.get(name)
        if not handler:
            logger.warning(f"MCP server: tool '{name}' not found")
            return CallToolResult(
                content=[TextContent(type="text", text=f"Tool '{name}' not found")],
                isError=True,
            )

        try:
            args = arguments or {}
            sig = inspect.signature(handler)
            if not sig.parameters:
                result = handler()
            elif not args:
                raise ValueError(f"Function '{name}' expects arguments but none were provided.")
            else:
                result = handler(**args)

            # Normalize result to string for MCP TextContent
            if isinstance(result, dict):
                result_text = json.dumps(result)
            elif isinstance(result, str):
                result_text = result
            else:
                result_text = str(result)

            return CallToolResult(
                content=[TextContent(type="text", text=result_text)],
                isError=False,
            )
        except Exception as e:
            logger.exception(f"MCP server: error executing tool '{name}': {e}")
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error executing '{name}': {e}")],
                isError=True,
            )

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def tool_to_openai_schema(self, tool: Tool) -> dict:
        """Convert an MCP Tool to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }

    def get_openai_tools(self, tool_names: Optional[List[str]] = None) -> List[dict]:
        """Get tools in OpenAI function calling format, optionally filtered."""
        tools = self.list_tools(tool_names)
        return [self.tool_to_openai_schema(t) for t in tools]
