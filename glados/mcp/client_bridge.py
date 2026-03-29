import asyncio
import json
import threading
from typing import Any, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.metadata import ToolMetadataRegistry
from glados.mcp.server import GladosMCPServer
from glados.system.intent_classifier import IntentClassifier
from glados.system.plugin import PluginSystem


class MCPClientBridge:
    """Connects to external MCP servers and registers their tools with GlaDOS.

    External MCP tools are discovered via the MCP protocol and registered
    with GladosMCPServer, ToolMetadataRegistry, and PluginSystem for
    seamless integration with activity filtering and tool execution.

    Since external MCP tools lack GlaDOS metadata, defaults are applied:
      - activity: [Activity.GENERAL]
      - process_output: True
      - intents: [] (no intent training)

    Configure in glados_config.yml:
        mcp_servers:
          - name: filesystem
            command: npx
            args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
          - name: custom
            command: python
            args: ["-m", "my_mcp_server"]
    """

    def __init__(self):
        self._bridges: Dict[str, "_ExternalServer"] = {}
        self._mcp_server = GladosMCPServer()
        self._metadata_registry = ToolMetadataRegistry()
        self._plugin_system = PluginSystem()

    def connect_server(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        activity: Optional[List[Activity]] = None,
        process_output: bool = True,
    ):
        """Connect to an external MCP server via stdio transport.

        Args:
            name: Unique name for this server connection.
            command: The command to run the MCP server (e.g., "npx", "python").
            args: Command arguments.
            env: Environment variables for the server process.
            activity: Default activity contexts for all tools from this server.
            process_output: Default process_output for all tools from this server.
        """
        activity = activity or [Activity.GENERAL]
        args = args or []

        try:
            # Import MCP client modules
            from mcp.client.stdio import StdioServerParameters

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
            )

            bridge = _ExternalServer(
                name=name,
                params=server_params,
                default_activity=activity,
                default_process_output=process_output,
            )

            # Run the async connection in a thread
            loop = asyncio.new_event_loop()
            tools = loop.run_until_complete(bridge.connect_and_discover())
            loop.close()

            # Register discovered tools with GlaDOS systems
            for tool in tools:
                tool_name = f"{name}__{tool['name']}"  # Namespace to avoid collisions

                # Register with MCP server (handler proxies to external server)
                handler = bridge.make_call_handler(tool['name'])
                self._mcp_server.register_tool(
                    name=tool_name,
                    description=tool.get('description', ''),
                    input_schema=tool.get('inputSchema', {"type": "object", "properties": {}}),
                    handler=handler,
                )

                # Register metadata with defaults
                self._metadata_registry.register(
                    name=tool_name,
                    handler=handler,
                    activity=activity,
                    process_output=process_output,
                )

                # Register with PluginSystem for backward compat
                from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters
                fr = FunctionRequest(
                    type="function",
                    function=FunctionMetadata(
                        name=tool_name,
                        description=tool.get('description', ''),
                        parameters=Parameters(
                            type="object",
                            properties={},
                            required=[],
                        ),
                    ),
                )
                self._plugin_system.plugins[tool_name] = {
                    "function": handler,
                    "description": tool.get('description', ''),
                    "llm_function_request": fr.to_dict() if hasattr(fr, 'to_dict') else {},
                    "process_output": process_output,
                    "callable": None,
                    "activity": activity,
                }

                logger.success(f"External MCP tool registered: {tool_name}")

            self._bridges[name] = bridge
            logger.info(f"Connected to external MCP server '{name}' with {len(tools)} tools")

        except Exception as e:
            logger.error(f"Failed to connect to external MCP server '{name}': {e}")

    def disconnect_server(self, name: str):
        """Disconnect from an external MCP server."""
        bridge = self._bridges.pop(name, None)
        if bridge:
            bridge.disconnect()
            logger.info(f"Disconnected from external MCP server '{name}'")

    def disconnect_all(self):
        """Disconnect from all external MCP servers."""
        for name in list(self._bridges.keys()):
            self.disconnect_server(name)


class _ExternalServer:
    """Internal wrapper for a single external MCP server connection."""

    def __init__(self, name, params, default_activity, default_process_output):
        self.name = name
        self.params = params
        self.default_activity = default_activity
        self.default_process_output = default_process_output
        self._process = None
        self._client = None

    async def connect_and_discover(self) -> List[Dict]:
        """Connect to the server and discover its tools."""
        from mcp.client.stdio import stdio_client
        from mcp.client.session import ClientSession

        try:
            async with stdio_client(self.params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tools = []
                    for tool in result.tools:
                        tools.append({
                            "name": tool.name,
                            "description": tool.description or "",
                            "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
                        })
                    self._client = session
                    return tools
        except Exception as e:
            logger.error(f"Error discovering tools from '{self.name}': {e}")
            return []

    def make_call_handler(self, tool_name: str):
        """Create a synchronous handler that proxies calls to the external MCP server."""
        server_name = self.name
        params = self.params

        def handler(**kwargs):
            async def _call():
                from mcp.client.stdio import stdio_client
                from mcp.client.session import ClientSession

                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, kwargs)
                        if result.isError:
                            text = result.content[0].text if result.content else "Unknown error"
                            return {"error": text}
                        text = result.content[0].text if result.content else ""
                        try:
                            return json.loads(text)
                        except (json.JSONDecodeError, TypeError):
                            return text

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_call())
            finally:
                loop.close()

        return handler

    def disconnect(self):
        """Clean up the connection."""
        self._client = None
