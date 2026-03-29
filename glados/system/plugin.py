import importlib
import os
from pathlib import Path
from typing import Callable, List

from loguru import logger

from glados.context.activity import Activity
from glados.llm.client_type import ClientType
from glados.mcp.adapter import MCPPluginAdapter
from glados.system.function_calling import FunctionRequest
from glados.system.event_system import EventSystem, EventMessage
from glados.system.intent_classifier import IntentClassifier
from glados.system.runnable_plugin import RunnablePlugin

LLM_FUNCTION_REQUEST = "llm_function_request"

event_system = EventSystem()


def load_plugins(package_path: str):
    """
    Dynamically load all modules in the given package directory and subdirectories.
    Automatically loads classes that subclass `RunnablePlugin`.

    Args:
        package_path (str): The directory path containing plugins (absolute or relative to the project root).
    """
    package_path = Path(package_path).resolve()
    logger.info(f"Loading plugins from: {package_path}")

    if not package_path.exists():
        logger.error(f"Plugin directory does not exist: {package_path}")
        return

    # Walk through the directory
    for root, _, files in os.walk(package_path):
        root_path = Path(root)
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                try:
                    # Convert file path to module path
                    relative_path = root_path.relative_to(package_path.parent)
                    module_name = ".".join(relative_path.parts + (file[:-3],))

                    # Import the module dynamically
                    module = importlib.import_module(module_name)
                    logger.success(f"Loaded plugin: {module_name}")

                    # Automatically instantiate subclasses of RunnablePlugin
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, RunnablePlugin) and attr is not RunnablePlugin and attr.__module__ != "glados.mcp.runnable_mcp_plugin":
                            PluginSystem().load_plugin_instance(attr)
                            logger.success(f"Loaded plugin instance: {attr_name}")

                except Exception as e:
                    logger.exception(f"Failed to load module {file[:-3]}: {e}")
                    event_system.publish(EventMessage(
                        "tool",
                        "plugin_system",
                        f"The plugin: {file[:-3]} has failed to load, please diagnose the issue",
                        process_output=True
                    ))


class PluginSystem:
    _instance = None  # Singleton instance
    _system_prompts = []  # future thing, so plugins can extend the system prompts.
    _ui_actions = {}  # event_name → list of callbacks (plugins self-register UI SocketIO handlers)

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PluginSystem, cls).__new__(cls)
            cls._instance._initialized = False  # Prevent multiple initializations
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.plugins = {}
        self.intent_classifier = IntentClassifier()
        self.mcp_adapter = MCPPluginAdapter()
        self._initialized = True

    def register(self, name: str = None,
                 description: str = None,
                 llm_function_request: FunctionRequest = None,
                 process_output: bool = True,
                 is_callable: Callable = None,
                 intents: List = None,
                 activity: List[Activity] = None,
                 nlp_threshold: float = None,
                 ):
        """
        Decorator to register a plugin with the given name, description, and parameters.

        Args:
            name (str): The name of the plugin (optional, derived from the function if not provided).
            description (str): A short description of the plugin (optional).
            llm_function_request (FunctionRequest, optional): A function/tool definition.
            process_output (bool): Whether to process the plugin output or pass it directly.
            intents: The intents for the plugin
            is_callable (bool): A function that can determine if this function is callable at a given moment
            activity (Activity): The context this tool fits into
        """

        def decorator(func: Callable):
            # Derive the name from the function if not provided
            plugin_name = name or func.__name__
            logger.debug(f"Plugin name: {plugin_name}")

            # Derive description from function_request or fall back to the decorator argument
            plugin_description = (
                llm_function_request.function.description
                if llm_function_request and llm_function_request.function.description
                else description or "Missing documentation, TODO fixme!"
            )
            logger.debug(f"Plugin description: {plugin_description}")

            # Update the function_request with the derived name if it exists
            if llm_function_request:
                logger.debug(f"Setting llm_function_request.function.name to {plugin_name}")
                llm_function_request.function.name = plugin_name

            logger.debug(f"Registering plugin: {plugin_name} with llm_function_request: {llm_function_request}")
            if llm_function_request and self.validate_plugin_definition(llm_function_request):
                # Resolve nlp_threshold: YAML config > code default > None
                effective_nlp_threshold = nlp_threshold
                try:
                    from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
                    yaml_config = RunnableMCPPlugin.get_plugin_config(plugin_name)
                    if "nlp_threshold" in yaml_config:
                        effective_nlp_threshold = float(yaml_config["nlp_threshold"])
                except Exception:
                    pass

                self.plugins[plugin_name] = {
                    "function": func,  # the function that the LLM can call
                    "description": plugin_description,  # The description of the funtion
                    LLM_FUNCTION_REQUEST: llm_function_request.to_dict() if llm_function_request else {},
                    "process_output": process_output,
                    "callable": is_callable,
                    "activity": activity or [Activity.GENERAL],
                    "nlp_threshold": effective_nlp_threshold,
                }
                logger.success(f"Registered plugin: {plugin_name}")
                logger.debug(f"plugin: {self.plugins[plugin_name]}")
                # Note: tool registration is NOT published to chat history because
                # tool definitions are already passed via the 'tools' API parameter
                # on every LLM call. Publishing here would duplicate them and bloat
                # the context, slowing down inference.

                # Also register with MCP adapter for MCP-compatible tool access
                self.mcp_adapter.register_tool(
                    name=plugin_name,
                    handler=func,
                    llm_function_request=llm_function_request,
                    intents=intents,
                    activity=activity or [Activity.GENERAL],
                    process_output=process_output,
                    callable_check=is_callable,
                )

            else:
                logger.warning(
                    f"Skipping plugin: {plugin_name}, due to validation failure. Check the FunctionRequest object.")

            if self.intent_classifier and intents:
                logger.debug(f"Plugin has intents: {intents}, sending them to the intent classifier")
                self.intent_classifier.add_intent(plugin_name, intents)
                self.intent_classifier.retrain()
            elif not intents:
                logger.debug(f"The plugin: {plugin_name} has no intents configured")

            if len(self.plugins) > 20:
                logger.error(
                    f"{len(self.plugins)} plugins are registered, this is more than 20 which is not recommended. "
                    f"See https://platform.openai.com/docs/guides/function-calling, However mitigations, like the activity "
                    f"system will make this a non-issue in future."
                )
            return func

        return decorator

    def get_intent_classifier(self):
        return self.intent_classifier

    def get_available_llm_functions(self):
        """Retrieves all llm functions for invocation locally, such as when a chat response names a tool to invoke"""
        available_functions = {}
        for k in self.plugins:
            if self.plugins[k][LLM_FUNCTION_REQUEST]:
                logger.debug(f"adding {k} to available plugins")
                available_functions[k] = self.plugins[k]['function']
            else:
                logger.debug(f"Skipping plugin instance '{k}' (no LLM function)")
        return available_functions

    def get_available_tools(self, architecture=ClientType.OPENAI, activity=None):
        """Returns all available and executable tools for passing into the LLM when calling it.
        When activity is None, returns all tools regardless of activity filter.
        SYSTEM tools are always included in every context."""
        logger.debug(f"request for tools: architecture: {architecture}, activity: {activity}")
        available_functions = []
        try:
            for k in self.plugins:
                if self.plugins[k][LLM_FUNCTION_REQUEST] != {}:
                    # If no activity filter, include all tools
                    if activity is not None and 'activity' in self.plugins[k]:
                        plugin_activities = self.plugins[k]['activity']
                        # SYSTEM tools are always available in every context
                        if activity not in plugin_activities and Activity.SYSTEM not in plugin_activities:
                            logger.debug(f"excluding tool: {k} due to {activity} not in activities: {plugin_activities}")
                            continue

                    if architecture is ClientType.LANGCHAIN:
                        available_functions.append(self.plugins[k]['function'])
                    else:
                        available_functions.append(self.plugins[k][LLM_FUNCTION_REQUEST])
                else:
                    logger.debug(f"Skipping plugin instance '{k}' (no LLM function definition)")
        except Exception as e:
            logger.exception(f"Error adding function: {k}, {e}")
        finally:
            return available_functions

    def get_nlp_threshold(self, tool_name: str, default: float = None) -> float:
        """Get the NLP confidence threshold for a tool.

        Resolution: per-tool value > default fallback.
        """
        plugin = self.plugins.get(tool_name)
        if plugin:
            threshold = plugin.get("nlp_threshold")
            if threshold is not None:
                return float(threshold)
        return default

    def should_process_plugin_output(self, name):
        if name in self.plugins:
            if self.plugins[name][LLM_FUNCTION_REQUEST]:
                logger.debug(f"Returning {self.plugins[name]}")
                return self.plugins[name]["process_output"]
            else:
                logger.debug(f"Plugin {name} is not a {LLM_FUNCTION_REQUEST}")
        else:
            logger.debug(f"Plugin {name} is not a plugins]")
        return False

    def get_plugin_metadata(self, name: str) -> dict:
        """
        Retrieve metadata for a specific plugin.

        Args:
            name (str): The plugin name.

        Returns:
            dict: The metadata of the plugin.
        """
        plugin = self.plugins.get(name)
        if not plugin:
            raise ValueError(f"Plugin '{name}' not found.")
        return {"name": name, **plugin}

    def list_plugins(self) -> dict:
        """
        List all registered plugins with metadata.

        Returns:
            dict: All plugins and their metadata.
        """
        return {
            name: {
                "description": plugin["description"],
                "parameters": plugin[LLM_FUNCTION_REQUEST],
            }
            for name, plugin in self.plugins.items()
        }

    def register_system_prompt(self, prompt):
        self._system_prompts.append(prompt)

    def get_system_prompts(self):
        return self._system_prompts

    def register_ui_action(self, event_name: str, callback):
        """Register a SocketIO UI action handler for a plugin.

        The DisplayPlugin auto-registers these as SocketIO event handlers.
        When the frontend emits the event, it's published to the EventSystem
        as 'ui.<event_name>' and the plugin's callback handles it.
        """
        logger.info(f"Registering UI action: {event_name}")
        self._ui_actions[event_name] = callback
        logger.success(f"Registered UI action: {event_name}")

    def get_ui_actions(self) -> dict:
        """Get all registered UI action handlers."""
        return self._ui_actions

    @staticmethod
    def validate_plugin_definition(plugin_definition: FunctionRequest) -> bool:
        """
        Validate the given FunctionRequest object.

        Args:
            plugin_definition (FunctionRequest): The plugin definition to validate.

        Returns:
            bool: True if the plugin definition is valid, False otherwise.
        """
        logger.debug(f"Validating plugin definition: {plugin_definition}")
        try:
            # Validate that the type is correct
            assert plugin_definition.type == "function", "Plugin type must be 'function'."

            # Validate the function metadata
            function = plugin_definition.function
            assert function.name, "Missing 'name' in function definition."
            assert function.description, "Missing 'description' in function definition."

            # Validate parameters
            parameters = function.parameters
            assert parameters.type == "object", "'parameters.type' must be 'object'."
            assert isinstance(parameters.properties, dict), "'parameters.properties' must be a dictionary."

            # Additional parameter validations (optional)
            if parameters.required:
                assert isinstance(parameters.required, list), "'parameters.required' must be a list."
                for param in parameters.required:
                    assert param in parameters.properties, f"Required parameter '{param}' must be in 'properties'."

            return True  # Plugin is valid
        except AssertionError as e:
            logger.error(f"Invalid plugin definition: {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during validation: {e}")
            return False

    def load_plugin_instance(self, cls, *args, **kwargs):
        """
        Instantiate and register a plugin class instance during plugin loading.

        Args:
            cls: The class to instantiate.
            *args: Positional arguments for class instantiation.
            **kwargs: Keyword arguments for class instantiation.
        """
        try:
            logger.info(f"Loading runnable plugin: {cls}")
            if not hasattr(cls, "__abstractmethods__") or not cls.__abstractmethods__:
                instance = cls(*args, **kwargs)
                plugin_name = cls.__name__.lower()  # Use the class name as the plugin name
                self.plugins[plugin_name] = {
                    "function": instance,
                    "description": getattr(cls, "__doc__", "No description available."),
                    LLM_FUNCTION_REQUEST: {}
                }
                logger.success(f"Loaded plugin runnable instance: {plugin_name}")

                # Automatically start RunnablePlugin instances
                if isinstance(instance, RunnablePlugin):
                    logger.info(f"Starting plugin: {plugin_name}")
                    instance.start()
                    logger.success(f"Started plugin: {plugin_name}")

                return instance
            else:
                logger.warning(f"Cannot instantiate abstract class {cls.__name__}")
        except Exception as e:
            logger.exception(f"Failed to load plugin instance for {cls.__name__}: {e}")
            return None
