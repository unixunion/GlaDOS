from typing import Callable, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.metadata import ToolMetadataRegistry
from glados.mcp.server import GladosMCPServer
from glados.system.function_calling import FunctionRequest


class MCPPluginAdapter:
    """Bridges the existing PluginSystem registration to MCP.

    Converts FunctionRequest (OpenAI schema) to MCP inputSchema,
    and registers tools with both GladosMCPServer and ToolMetadataRegistry.
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
        self.mcp_server = GladosMCPServer()
        self.metadata_registry = ToolMetadataRegistry()
        self._initialized = True

    def register_tool(
        self,
        name: str,
        handler: Callable,
        llm_function_request: FunctionRequest,
        intents: Optional[List[str]] = None,
        activity: Optional[List[Activity]] = None,
        process_output: bool = True,
        callable_check: Optional[Callable] = None,
    ):
        """Register a plugin tool with both MCP server and metadata registry.

        Args:
            name: Tool name
            handler: The Python function to invoke
            llm_function_request: Existing OpenAI-format tool definition
            intents: Intent examples for the IntentClassifier
            activity: Activity contexts this tool belongs to
            process_output: Whether tool results should be processed by the LLM
            callable_check: Optional function to check if tool is currently available
        """
        # Convert FunctionRequest to MCP inputSchema
        description = llm_function_request.function.description or ""
        input_schema = self._function_request_to_input_schema(llm_function_request)

        # Register with MCP server (tool schema + handler)
        self.mcp_server.register_tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

        # Register GlaDOS-specific metadata
        self.metadata_registry.register(
            name=name,
            handler=handler,
            intents=intents,
            activity=activity,
            process_output=process_output,
            callable_check=callable_check,
        )

        logger.debug(f"MCPPluginAdapter: registered '{name}' with MCP server and metadata registry")

    @staticmethod
    def _function_request_to_input_schema(fr: FunctionRequest) -> dict:
        """Convert a FunctionRequest's parameters to MCP-compatible JSON Schema.

        Both OpenAI and MCP use JSON Schema for parameters, so this is
        mostly a structural reshaping of the dataclass fields.
        """
        params = fr.function.parameters
        properties = {}
        for prop_name, prop_type in params.properties.items():
            prop_schema: Dict = {"type": prop_type.type}
            if prop_type.description:
                prop_schema["description"] = prop_type.description
            if prop_type.enum:
                prop_schema["enum"] = prop_type.enum
            properties[prop_name] = prop_schema

        schema = {
            "type": params.type,
            "properties": properties,
        }
        if params.required:
            schema["required"] = params.required
        if params.additionalProperties is not None:
            schema["additionalProperties"] = params.additionalProperties

        return schema
