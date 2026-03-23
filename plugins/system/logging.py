from typing import List

from loguru import logger

from glados.context.activity import Activity
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters
from glados.system.event_system import EventSystem, EventMessage, EventHook
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()


def _logs_nlp_response(result) -> str:
    """Format log entries for TTS."""
    if isinstance(result, list):
        count = len(result)
        if count == 0:
            return "No log entries found."
        return f"There are {count} log entries recorded."
    return "I checked the logs."


class LoggingPlugin(RunnablePlugin):

    MAX_LOG_SIZE = 1000

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
                "run a self diagnostic",
                "show me the logs",
                "are there any errors",
                "any warnings in the logs",
                "system status",
                "check for problems",
                "get recent logs",
            ],
            process_output=True,
            activity=[Activity.SYSTEM]
        )(self.get_logs)

        # Register NLP handler
        _nlp_registry = NLPHandlerRegistry()
        _nlp_registry.register(NLPHandler(
            tool_name="get_logs",
            response_fn=_logs_nlp_response,
        ))

        logger.success("Started")

    def stop(self):
        self.event_system.unsubscribe("log.*", "event_log")

    def start(self):
        self.event_system.subscribe("log.*", EventHook("event_log", self.journal))

    def journal(self, event: EventMessage):
        logger.info(f"Appending {event} to the log")
        self.event_log.append(event)
        if len(self.event_log) > self.MAX_LOG_SIZE:
            self.event_log = self.event_log[-self.MAX_LOG_SIZE:]

    def get_logs(self):
        response = []
        for event in self.event_log:
            response.append(event.to_json())
        return response
