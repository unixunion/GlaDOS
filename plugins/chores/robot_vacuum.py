from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()
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
                    description="Start the robot vacuum cleaner to clean the floors and carpets",
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
                "start the vacuum cleaner",
                "clean the kitchen",
                "vacuum the carpets",
                "Start the robot vacuum cleaner",
                "Start cleaning the living room",
                "Send the vacuum robot to the kitchen"
            ],
            process_output=True,
            activity=[Activity.CHORES]
        )(self.start_vacuuming)

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Stops the robot vacuum cleaner",
                    parameters=Parameters(
                        type="object",
                        properties={},
                        required=[],
                        additionalProperties=False,
                    ),
                ),
            ),
            intents=[
                "Stop vacuuming",
                "Send the vacuum cleaner to the doc",
                "Cancel cleaning",
                "Stop the vacuum cleaner",
                "stop vacuuming the floors",
                "stop the roomba"
            ],
            process_output=True,
            activity=[Activity.CHORES]
        )(self.stop_vacuuming)

    def start_vacuuming(self) -> dict:
        if not self.is_vacuuming:
            self.is_vacuuming = True
            event_system.publish(EventMessage(
                role="tool",
                name="start_vacuuming",
                content="Robot vacuum started"
            ))
            return {'status': 'the vacuum robot has started'}
        else:
            logger.info("Already running the vacuum")
            return {'status': 'the vacuum robot is already running'}

    def stop_vacuuming(self) -> dict:
        if self.is_vacuuming:
            self.is_vacuuming = False
            event_system.publish(EventMessage(
                role="tool",
                name="stop_vacuuming",
                content="Robot vacuum has finished"
            ))
            return {'status': 'the robot vacuum is stopped'}
        else:
            return {'status': 'the robot vacuum is already stopped'}

    def start(self):
        logger.info("Starting RobotVacuum plugin.")

    def stop(self):
        logger.info("Stopping RobotVacuum plugin.")
