from abc import ABC, abstractmethod


class RunnablePlugin(ABC):
    """
    Base class for plugins that can be run and managed by the PluginManager.
    Defines lifecycle hooks for initialization, execution, and cleanup.
    """

    def __init__(self):
        """
        Initialize the plugin. This can include setting up resources or default states.
        """
        self.name = self.__class__.__name__  # Default to the class name as the plugin name
        self.is_active = False  # Track whether the plugin is actively running

    @abstractmethod
    def start(self):
        """
        Start the plugin. This method should be overridden by derived classes
        to define the plugin's startup logic.
        """
        pass

    @abstractmethod
    def stop(self):
        """
        Stop the plugin. This method should be overridden by derived classes
        to define cleanup or shutdown logic.
        """
        pass

    def status(self):
        """
        Get the current status of the plugin.
        Returns:
            str: Status message indicating whether the plugin is active.
        """
        return f"Plugin '{self.name}' is {'active' if self.is_active else 'inactive'}."
