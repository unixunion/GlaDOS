from typing import Callable, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.metadata import ToolMetadataRegistry
from glados.mcp.server import GladosMCPServer
from glados.system.intent_classifier import IntentClassifier
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem


def mcp_tool(
    description: str,
    parameters: Optional[Dict[str, dict]] = None,
    required: Optional[List[str]] = None,
    intents: Optional[List[str]] = None,
    activity: Optional[List[Activity]] = None,
    process_output: bool = True,
    system_prompt: Optional[str] = None,
):
    """MCP-native tool registration decorator.

    Registers the tool with:
      - GladosMCPServer (MCP tool schema + handler)
      - ToolMetadataRegistry (intents, activity, process_output)
      - IntentClassifier (if intents provided)
      - PluginSystem (backward compat)

    Args:
        system_prompt: Optional text appended to the system prompt to guide LLM
                      behavior when using this tool.

    Usage:
        @mcp_tool(
            description="Get current weather for a location.",
            parameters={"location": {"type": "string", "description": "City name"}},
            required=["location"],
            intents=["what is the weather", "is it cold today"],
            activity=[Activity.GENERAL, Activity.UTILITIES],
            process_output=True,
            system_prompt="When reporting weather, include temperature and conditions.",
        )
        def handle_weather(location: str) -> str:
            return "Sunny, 25C"
    """
    activity = activity or [Activity.GENERAL]
    parameters = parameters or {}

    def decorator(func: Callable):
        name = func.__name__

        # Build MCP inputSchema from the simplified parameters dict
        input_schema = {
            "type": "object",
            "properties": parameters,
        }
        if required:
            input_schema["required"] = required

        # Register with MCP server
        mcp_server = GladosMCPServer()
        mcp_server.register_tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=func,
        )

        # Register GlaDOS metadata
        registry = ToolMetadataRegistry()
        registry.register(
            name=name,
            handler=func,
            intents=intents,
            activity=activity,
            process_output=process_output,
        )

        # Register with IntentClassifier
        classifier = IntentClassifier()
        if intents:
            classifier.add_intent(name, intents)
            classifier.retrain()

        # Backward compat: also register with legacy PluginSystem
        # Build a FunctionRequest so PluginSystem.plugins dict stays populated
        properties = {}
        for param_name, param_def in parameters.items():
            properties[param_name] = ParameterType(
                type=param_def.get("type", "string"),
                description=param_def.get("description"),
                enum=param_def.get("enum"),
            )

        fr = FunctionRequest(
            type="function",
            function=FunctionMetadata(
                name=name,
                description=description,
                parameters=Parameters(
                    type="object",
                    properties=properties,
                    required=required or [],
                    additionalProperties=False,
                ),
            ),
        )

        plugin_system = PluginSystem()

        # Register system prompt addition if provided
        if system_prompt:
            plugin_system.register_system_prompt(system_prompt)

        plugin_system.plugins[name] = {
            "function": func,
            "description": description,
            "llm_function_request": fr.to_dict() if hasattr(fr, 'to_dict') else {},
            "process_output": process_output,
            "callable": None,
            "activity": activity,
        }

        logger.success(f"MCP tool registered: {name}")
        return func

    return decorator
