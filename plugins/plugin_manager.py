import importlib
import os
from typing import Callable

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

    def __init__(self):
        self.plugins = {}  # Registry of plugins
        self.pre_init_hooks = []  # List of pre-initialization hooks

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PluginManager, cls).__new__(cls)
            cls._instance.plugins = {}  # Initialize the plugin registry
        return cls._instance

    def register(self, name: str):
        """
        Decorator to register a plugin with the given name.

        Args:
            name (str): The name of the plugin.
        """

        def decorator(func: Callable):
            logger.success(f"Registering plugin: {name}")
            self.plugins[name] = func
            logger.success(f"Plugins: {self.plugins}")
            return func

        return decorator

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
            raise ValueError(f"Plugin '{name}' not found.")
        logger.success(f"Calling plugin '{name}' with args: {args}, kwargs: {kwargs}")
        return plugin(*args, **kwargs)

    def pre_initialize_plugins(self, context):
        """
        Run pre-initialization hooks for all registered plugins.

        Args:
            context: The context to pass to pre-initialization functions.
        """
        for hook in self.pre_init_hooks:
            try:
                hook(context)
            except Exception as e:
                logger.error(f"Error during plugin pre-initialization: {e}")

    def add_pre_init_hook(self, hook: Callable):
        """
        Register a pre-initialization hook.

        Args:
            hook (Callable): A function to be run during pre-initialization.
        """
        self.pre_init_hooks.append(hook)
        logger.info(f"Registered pre-init hook: {hook.__name__}")

    def initialize_plugins(self, *args, **kwargs):
        """
        Calls the initialize hook for all loaded plugins.
        """
        for name, plugin in self.plugins.items():
            if hasattr(plugin, 'initialize'):
                plugin.initialize(*args, **kwargs)


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
