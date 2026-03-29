import re
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.metadata import ToolMetadataRegistry
from glados.mcp.server import GladosMCPServer
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry
from glados.system.event_system import EventSystem
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.intent_classifier import IntentClassifier
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin


class RunnableMCPPlugin(RunnablePlugin):
    """Base class for stateful plugins that register tools via MCP.

    Combines RunnablePlugin lifecycle (start/stop) and EventSystem access
    with MCP-native tool registration. Use this instead of manually calling
    plugin_manager.register() with verbose FunctionRequest chains.

    Tools registered via self.register_tool() are added to:
      - GladosMCPServer (tool schema + handler)
      - ToolMetadataRegistry (intents, activity, process_output)
      - IntentClassifier (if intents provided)
      - PluginSystem (backward compat)

    Plugin config is loaded from glados_config.yml under the `plugins` key,
    matched by the lowercase class name. Access via self.plugin_config.

    System prompt additions can be registered via self.register_system_prompt().
    """

    def __init__(self):
        super().__init__()
        self.event_system = EventSystem()
        self._mcp_server = GladosMCPServer()
        self._metadata_registry = ToolMetadataRegistry()
        self._intent_classifier = IntentClassifier()
        self._plugin_system = PluginSystem()
        self.plugin_config: Dict[str, Any] = self._load_plugin_config()

    def _load_plugin_config(self) -> Dict[str, Any]:
        """Load this plugin's config from the `plugins` section of glados_config.yml.

        Matches the `name` field in each plugin config entry against the class name
        (case-insensitive, underscores ignored). Tool-name matching happens later
        via get_plugin_config() for function-based plugins.

        Example glados_config.yml:
            plugins:
              - name: music_player
                config:
                  default_volume: 50
              - name: alarm_clock
                config:
                  default_sound: chime
        """
        try:
            from glados.config import GladosConfig
            config = GladosConfig.from_yaml("glados_config.yml")
            plugin_name = self.__class__.__name__.lower().replace("_", "")

            for plugin_entry in (config.plugins or []):
                entry_name = plugin_entry.get("name", "") if isinstance(plugin_entry, dict) else getattr(plugin_entry, "name", "")
                entry_normalized = entry_name.lower().replace("_", "")
                if entry_normalized == plugin_name:
                    cfg = plugin_entry.get("config", {}) if isinstance(plugin_entry, dict) else getattr(plugin_entry, "config", {})
                    logger.info(f"Loaded plugin config for {self.__class__.__name__}: {cfg}")
                    return cfg or {}
        except Exception as e:
            logger.debug(f"Could not load plugin config for {self.__class__.__name__}: {e}")
        return {}

    @staticmethod
    def get_plugin_config(name: str) -> Dict[str, Any]:
        """Load config for a named plugin/tool from glados_config.yml.

        For use by function-based plugins (via @mcp_tool) that don't have a class.
        Matches the `name` field in the plugins config section.

        Usage:
            config = RunnableMCPPlugin.get_plugin_config("search_recipes")
        """
        try:
            from glados.config import GladosConfig
            glados_config = GladosConfig.from_yaml("glados_config.yml")
            name_normalized = name.lower().replace("_", "")

            for plugin_entry in (glados_config.plugins or []):
                entry_name = plugin_entry.get("name", "") if isinstance(plugin_entry, dict) else getattr(plugin_entry, "name", "")
                if entry_name.lower().replace("_", "") == name_normalized:
                    cfg = plugin_entry.get("config", {}) if isinstance(plugin_entry, dict) else getattr(plugin_entry, "config", {})
                    return cfg or {}
        except Exception as e:
            logger.debug(f"Could not load plugin config for {name}: {e}")
        return {}

    def register_system_prompt(self, prompt: str):
        """Register additional text to be appended to the system prompt.

        Use this to give the LLM guidance on how to interact with your plugin's tools.
        """
        self._plugin_system.register_system_prompt(prompt)
        logger.info(f"System prompt addition registered by {self.__class__.__name__}")

    def register_ui_action(self, event_name: str, callback: Callable):
        """Register a SocketIO UI action handler.

        The DisplayPlugin auto-creates SocketIO event handlers for these.
        When the frontend emits the event, it's published to the EventSystem
        and the callback is triggered. The plugin should also subscribe to
        'ui.<event_name>' in start() to handle the events.

        Args:
            event_name: SocketIO event name (e.g. 'recipe_action', 'pantry_action')
            callback: Function to handle the event data
        """
        self._plugin_system.register_ui_action(event_name, callback)
        logger.info(f"UI action '{event_name}' registered by {self.__class__.__name__}")

    def register_tool(
        self,
        handler: Callable,
        description: str,
        parameters: Optional[Dict[str, dict]] = None,
        required: Optional[List[str]] = None,
        intents: Optional[List[str]] = None,
        activity: Optional[List[Activity]] = None,
        process_output: bool = True,
        name: Optional[str] = None,
        nlp_extractors: Optional[Dict[str, list]] = None,
        nlp_response: Optional[Callable[[Any], str]] = None,
        nlp_extract_fn: Optional[Callable[[str], dict]] = None,
        nlp_threshold: Optional[float] = None,
    ):
        """Register a tool with MCP server and all GlaDOS systems.

        Args:
            handler: The method to call when the tool is invoked.
            description: Human-readable description of the tool.
            parameters: Dict of parameter definitions, e.g.
                        {"location": {"type": "string", "description": "City name"}}
            required: List of required parameter names.
            intents: Example phrases for the IntentClassifier.
            activity: Activity contexts this tool belongs to.
            process_output: Whether tool results should be processed by the LLM.
            name: Tool name (defaults to handler.__name__).
        """
        tool_name = name or handler.__name__
        activity = activity or [Activity.GENERAL]
        parameters = parameters or {}

        # Build MCP inputSchema
        input_schema = {
            "type": "object",
            "properties": parameters,
        }
        if required:
            input_schema["required"] = required

        # Register with MCP server
        self._mcp_server.register_tool(
            name=tool_name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

        # Register GlaDOS metadata
        self._metadata_registry.register(
            name=tool_name,
            handler=handler,
            intents=intents,
            activity=activity,
            process_output=process_output,
        )

        # Register intents
        if intents:
            self._intent_classifier.add_intent(tool_name, intents)
            self._intent_classifier.retrain()

        # Backward compat: populate PluginSystem.plugins dict
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
                name=tool_name,
                description=description,
                parameters=Parameters(
                    type="object",
                    properties=properties,
                    required=required or [],
                    additionalProperties=False,
                ),
            ),
        )

        # Resolve nlp_threshold: YAML config > code default > None (global fallback)
        effective_nlp_threshold = nlp_threshold
        yaml_threshold = self.plugin_config.get("nlp_threshold")
        if yaml_threshold is not None:
            effective_nlp_threshold = float(yaml_threshold)

        self._plugin_system.plugins[tool_name] = {
            "function": handler,
            "description": description,
            "llm_function_request": fr.to_dict() if hasattr(fr, 'to_dict') else {},
            "process_output": process_output,
            "callable": None,
            "activity": activity,
            "nlp_threshold": effective_nlp_threshold,
        }

        # Register NLP handler if extractors, extract_fn, or response formatter provided
        if nlp_extractors is not None or nlp_response is not None or nlp_extract_fn is not None:
            compiled_extractors = {}
            for param_name, patterns in (nlp_extractors or {}).items():
                compiled_extractors[param_name] = [
                    p if isinstance(p, re.Pattern) else re.compile(p, re.IGNORECASE)
                    for p in patterns
                ]
            nlp_handler = NLPHandler(
                tool_name=tool_name,
                extractors=compiled_extractors,
                response_fn=nlp_response,
                extract_fn=nlp_extract_fn,
            )
            NLPHandlerRegistry().register(nlp_handler)

        logger.success(f"MCP tool registered: {tool_name} (from {self.__class__.__name__})")

    def register_chat_hook(self, phase, callback: Callable, priority: int = 0, name: str = None):
        """Register a hook into the chat pipeline.

        Args:
            phase: ChatPipelinePhase (PRE_LLM, POST_TOOL, POST_RESPONSE)
            callback: function(ChatContext) -> None. Modify ctx to influence pipeline.
            priority: lower runs first (default 0)
            name: hook name for logging (defaults to class_name.phase)
        """
        from glados.llm.chat_hooks import ChatHookRegistry, ChatHook
        hook_name = name or f"{self.__class__.__name__}.{phase.value}"
        ChatHookRegistry().register(ChatHook(
            name=hook_name,
            phase=phase,
            callback=callback,
            priority=priority,
        ))
