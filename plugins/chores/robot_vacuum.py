from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters
from plugins.event_system.event_system import EventSystem, EventMessage
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()
event_system = EventSystem()


class RobotVacuum(RunnablePlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            logger.info("Instantiating singleton")
            cls._instance = super(RobotVacuum, cls).__new__(cls)
            cls._instance._initialized = False  # Ensure this is only done once
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()
        self.is_vacuuming = False
        logger.info("RobotVacuum system initializing.")

        # Register LLM functions
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Start the robot vacuum cleaner to vacuum the clean the floors and carpets and remove dirt",
                    parameters=Parameters(
                        type="object",
                        properties={},
                        required=[],
                        additionalProperties=False,
                    ),
                ),
            ),
            intents=[
                "Start vacuuming",
                "Start the robot vacuum cleaner",
                "Start cleaning the living room",
                "Send the vacuum robot to the kitchen"
            ],
            process_output=True,
        )(self.start_vacuuming)

    def start_vacuuming(self):
        if not self.is_vacuuming:
            self.is_vacuuming = True
            event_system.publish(EventMessage(
                role="tool",
                name="start_vacuuming",
                content="Robot vacuum started"
            ))
        else:
            logger.info("Already running the vacuum")

    def stop_vacuuming(self):
        self.is_vacuuming = False
        event_system.publish(EventMessage(
            role="tool",
            name="start_vacuuming",
            content="Robot vacuum has finished"
        ))

    def start(self):
        logger.info("Starting RobotVacuum plugin.")

    def stop(self):
        logger.info("Stopping RobotVacuum plugin.")
