from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage


class RobotVacuum(RunnableMCPPlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            logger.info("Instantiating singleton")
            cls._instance = super(RobotVacuum, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()
        self.is_vacuuming = False
        logger.info("RobotVacuum system initializing.")

        self.register_tool(
            handler=self.start_vacuuming,
            description="Start the robot vacuum cleaner to clean the floors and carpets",
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
            activity=[Activity.CHORES],
            nlp_response=lambda r: r.get("status", "Vacuum started."),
        )

        self.register_tool(
            handler=self.stop_vacuuming,
            description="Stops the robot vacuum cleaner",
            intents=[
                "Stop vacuuming",
                "Send the vacuum cleaner to the doc",
                "Cancel cleaning",
                "Stop the vacuum cleaner",
                "stop vacuuming the floors",
                "stop the roomba"
            ],
            process_output=True,
            activity=[Activity.CHORES],
            nlp_response=lambda r: r.get("status", "Vacuum stopped."),
        )

    def start_vacuuming(self) -> dict:
        if not self.is_vacuuming:
            self.is_vacuuming = True
            self.event_system.publish(EventMessage(
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
            self.event_system.publish(EventMessage(
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
