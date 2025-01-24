from typing import List

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem, EventMessage, EventHook
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()


class LoggingPlugin(RunnablePlugin):

    def __init__(self):
        super().__init__()
        self.event_system = EventSystem()
        self.event_log: List[EventMessage] = []
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Gets plugin, function and tool handle error logs",
                    parameters=Parameters(
                        type="object",
                        properties={},
                        required=[],
                        additionalProperties=False
                    )
                )),
            intents=[
                "get the plugin error logs",
                "retrieve the logs",
                "check logs for errors",
                "run a self diagnostic"
            ],
            process_output=True
        )(self.get_logs)
        logger.success("Started")

    def stop(self):
        self.event_system.unsubscribe("log.*", "event_log")

    def start(self):
        self.event_system.subscribe("log.*", EventHook("event_log", self.journal))

    def journal(self, event: EventMessage):
        logger.info(f"Appending {event} to the log")
        self.event_log.append(event)

    def get_logs(self):
        response = []
        for event in self.event_log:
            response.append(event.to_json())
        return response
