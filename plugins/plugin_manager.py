import importlib
import os
import queue
import threading
from typing import Callable, Tuple, Any

from loguru import logger


def load_plugins(package: str):
    """
    Dynamically load all modules in the given package and subdirectories.

    Args:
        package (str): The package name (e.g., "plugins").
    """
    package_path = package.replace(".", "/")
    for root, dirs, files in os.walk(package_path):
        module_path = root.replace("/", ".").replace("\\", ".")
        if "__init__.py" in files:
            # Load __init__.py for directories
            try:
                importlib.import_module(module_path)
                logger.success(f"Loaded module: {module_path}")
            except Exception as e:
                logger.error(f"Failed to load module {module_path}: {e}")
        else:
            # Load individual Python files
            for file in files:
                if file.endswith(".py"):
                    module_name = f"{module_path}.{file[:-3]}"
                    try:
                        importlib.import_module(module_name)
                        logger.success(f"Loaded module: {module_name}")
                    except Exception as e:
                        logger.error(f"Failed to load module {module_name}: {e}")


class PluginManager:
    _instance = None  # Singleton instance

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

    def register(self, name: str, description: str, function_request: dict = None):
        """
        Decorator to register a plugin with the given name, description, and parameters.

        Args:
            name (str): The name of the plugin.
            description (str): A short description of the plugin.
            function_request (dict, optional): A function / tool definition

        """

        def decorator(func: Callable):
            logger.success(f"Registering plugin: {name}")
            self.plugins[name] = {
                "function": func,
                "description": description,
                "function_request": function_request or {},
            }
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

    def execute_plugin_and_wait(self, name: str, args: Tuple = (), kwargs: dict = {}) -> Any:
        """
        Executes a plugin synchronously and waits for the result.

        Args:
            name (str): The name of the plugin to execute.
            args (Tuple): Positional arguments for the plugin.
            kwargs (dict): Keyword arguments for the plugin.

        Returns:
            Any: The result from the plugin execution.
        """
        result_queue = queue.Queue()  # Queue to hold the plugin's result

        def callback(result):
            result_queue.put(result)

        # Ensure the plugin exists
        if name not in self.plugins:
            raise ValueError(f"Plugin '{name}' not found.")

        logger.info(f"Executing plugin '{name}' synchronously with args={args}, kwargs={kwargs}")
        plugin = self.plugins[name]['function']

        # Execute the plugin
        threading.Thread(target=lambda: callback(plugin(*args, **kwargs))).start()

        # Wait for the plugin result
        return result_queue.get()


# class PluginManager:
#     _instance = None  # Singleton instance
#
#     def __init__(self):
#         self.plugins = {}  # Registry of plugins
#         self.pre_init_hooks = []  # List of pre-initialization hooks
#
#     def __new__(cls, *args, **kwargs):
#         if not cls._instance:
#             cls._instance = super(PluginManager, cls).__new__(cls)
#             cls._instance.plugins = {}  # Initialize the plugin registry
#         return cls._instance
#
#     def register(self, name: str, description: str):
#         """
#         Decorator to register a plugin with the given name.
#
#         Args:
#             name (str): The name of the plugin.
#         """
#
#         def decorator(func: Callable):
#             logger.success(f"Registering plugin: {name}")
#             self.plugins[name] = {"function": func, "description": description}
#             logger.success(f"Plugins: {self.plugins}")
#             return func
#
#         return decorator
#
#     def execute(self, name: str, *args, **kwargs):
#         """
#         Execute the plugin by name with provided arguments.
#
#         Args:
#             name (str): The name of the plugin.
#         Returns:
#             The result of the plugin function.
#         """
#         plugin = self.plugins.get(name)
#         if not plugin:
#             raise ValueError(f"Plugin '{name}' not found.")
#         logger.success(f"Calling plugin '{name}' with args: {args}, kwargs: {kwargs}")
#         return plugin['function'](*args, **kwargs)
#
#     def pre_initialize_plugins(self, context):
#         """
#         Run pre-initialization hooks for all registered plugins.
#
#         Args:
#             context: The context to pass to pre-initialization functions.
#         """
#         for hook in self.pre_init_hooks:
#             try:
#                 hook(context)
#             except Exception as e:
#                 logger.error(f"Error during plugin pre-initialization: {e}")
#
#     def add_pre_init_hook(self, hook: Callable):
#         """
#         Register a pre-initialization hook.
#
#         Args:
#             hook (Callable): A function to be run during pre-initialization.
#         """
#         self.pre_init_hooks.append(hook)
#         logger.info(f"Registered pre-init hook: {hook.__name__}")
#
#     def initialize_plugins(self, *args, **kwargs):
#         """
#         Calls the initialize hook for all loaded plugins.
#         """
#         for name, plugin in self.plugins.items():
#             if hasattr(plugin, 'initialize'):
#                 plugin.initialize(*args, **kwargs)


class BasePlugin:
    def pre_initialize(self, llm, *args, **kwargs):
        """
        Hook for performing tasks with the LLM before full initialization.
        Args:
            llm: The low-level LLM instance or endpoint.
        """
        pass

    def initialize(self, *args, **kwargs):
        """
        Hook for performing tasks after full initialization.
        """
        pass
