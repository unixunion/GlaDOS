import importlib
import os
import queue
import threading
from typing import Callable, Tuple, Any

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType

def load_plugins(package: str):
    """
    Dynamically load all modules in the given package and subdirectories.

    Args:
        package (str): The package name (e.g., "plugins").
    """
    package_path = package.replace(".", "/")
    for root, _, files in os.walk(package_path):
        # Construct the module path
        module_path = root.replace("/", ".").replace("\\", ".")

        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                module_name = f"{module_path}.{file[:-3]}"
                try:
                    importlib.import_module(module_name)
                    logger.success(f"Loaded module: {module_name}")
                except Exception as e:
                    logger.error(f"Failed to load module {module_name}: {e}")
    # package_path = package.replace(".", "/")
    # for root, dirs, files in os.walk(package_path):
    #     module_path = root.replace("/", ".").replace("\\", ".")
    #     if "__init__.py" in files:
    #         # Load __init__.py for directories
    #         try:
    #             importlib.import_module(module_path)
    #             logger.success(f"Loaded module: {module_path}")
    #         except Exception as e:
    #             logger.error(f"Failed to load module {module_path}: {e}")
    #     else:
    #         # Load individual Python files
    #         for file in files:
    #             if file.endswith(".py"):
    #                 module_name = f"{module_path}.{file[:-3]}"
    #                 try:
    #                     importlib.import_module(module_name)
    #                     logger.success(f"Loaded module: {module_name}")
    #                 except Exception as e:
    #                     logger.error(f"Failed to load module {module_name}: {e}")


class PluginManager:
    _instance = None  # Singleton instance
    _system_prompts = []

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PluginManager, cls).__new__(cls)
            cls._instance._initialized = False  # Prevent multiple initializations
        return cls._instance

    def __init__(self):
        if self._initialized:
            return  # Skip re-initialization

        self.plugins = {}  # Registry of plugins
        self.pre_init_hooks = []  # List of pre-initialization hooks
        self._initialized = True  # Mark as initialized

    def register(self, name: str, description: str, function_request: dict = None, process_output: bool = True):
        """
        Decorator to register a plugin with the given name, description, and parameters.

        Args:
            name (str): The name of the plugin.
            description (str): A short description of the plugin.
            function_request (dict, optional): A function / tool definition
            process_output: if the llm should interpret the data or just feed it to the tts
        """

        def decorator(func: Callable):
            logger.info(f"Registering plugin definition: {function_request}")
            if self.validate_plugin_definition(plugin_definition=function_request):
                self.plugins[name] = {
                    "function": func,
                    "description": description,
                    "function_request": function_request or {},
                    "process_output": process_output
                }
                logger.success(f"Registering plugin: {name}")
            else:
                logger.warning(f"Skipping plugin: {name}, due to validation failure, check the FunctionRequest object")
            if len(self.plugins)>20:
                logger.warning("More than 20 plugins are registered, this is not recommended, see https://platform.openai.com/docs/guides/function-calling")
            return func

        return decorator

    def get_available_plugins(self):
        available_functions = {}
        for k in self.plugins:
            available_functions[k] = self.plugins[k]['function']
        return available_functions

    def get_available_plugins_as_list(self):
        available_functions = []
        for k in self.plugins:
            available_functions.append(self.plugins[k]['function_request'])
        return available_functions

    def should_process_plugin_output(self, name):
        if name in self.plugins:
            return self.plugins[name]["process_output"]
        else:
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
                "parameters": plugin["function_request"],
            }
            for name, plugin in self.plugins.items()
        }

    def execute(self, name: str, *args, **kwargs):
        """
        Execute the plugin by name with provided arguments.

        Args:
            name (str): The name of the plugin.
        Returns:
            The result of the plugin function.
        """
        plugin = self.plugins.get(name)
        if not plugin:
            raise ValueError(f"Plugin '{name}' not found in {self.plugins}")
        logger.success(f"Calling plugin '{name}' with args: {args}, kwargs: {kwargs}")
        return plugin["function"](*args, **kwargs)

    def register_system_prompt(self, prompt):
        self._system_prompts.append(prompt)

    def get_system_prompts(self):
        return self._system_prompts

    def validate_plugin_definition(self, plugin_definition: dict) -> bool:
        try:
            # Example validations
            assert "type" in plugin_definition, "Missing 'type' in plugin definition."
            assert plugin_definition["type"] == "function", "Unsupported plugin type."
            assert "function" in plugin_definition, "Missing 'function' in plugin definition."

            function = plugin_definition["function"]
            assert "name" in function, "Missing 'name' in function definition."
            assert "description" in function, "Missing 'description' in function definition."
            assert "parameters" in function, "Missing 'parameters' in function definition."

            parameters = function["parameters"]
            assert parameters["type"] == "object", "'parameters.type' must be 'object'."
            assert isinstance(parameters["properties"], dict), "'parameters.properties' must be a dictionary."

            # Additional validations as needed...
            return True  # Plugin is valid
        except AssertionError as e:
            logger.error(f"Invalid plugin definition: {e}")
            return False


plugin_manager = PluginManager()

list_plugins_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='list_plugins',
                        description="List all plugins, integrations and functions currently loaded into the home assistant architecture",
                        parameters=Parameters(type="object", required=[], properties={}),
                    )
                    )
).to_dict()
@plugin_manager.register(
        "list_plugins",
        "Lists all available plugins.",
        list_plugins_definition,
    )
def list_plugins() -> str:
    try:
        return f"Describe the current plugins / integrations / functions registered with this architecture:\n\n {plugin_manager.list_plugins()}"
    except Exception as e:
        logger.error(f"Unable to list plugins, error was {e}")