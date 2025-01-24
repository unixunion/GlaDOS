import threading
import time

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem, EventMessage
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()  # the plugin manager
event_system = EventSystem()  # allows us to send stuff to the LLM whenever we want


class MyRunnablePlugin(RunnablePlugin):
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._worker_thread = None

        # register a llm function we can call from the llm
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                function=FunctionMetadata(
                    description="Hello World, greets the responder by name if known, usage example: 'hello world, "
                                "my name is george'",
                    parameters=Parameters(type="object", required=['name'], properties={
                        'name': ParameterType(type="string", description="name to acknowledge")
                    })
                )),
            intents=[
                "run the hello world plugin",
                "hello world, my name is kegan",
                "invoke the hello world function"
            ],
            process_output=True  # process output via llm model inference,
        )(self.hello_world)

    def start(self):
        logger.info("Starting...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def ticker():
            while not self._stop_event.is_set():
                time.sleep(100)
                logger.info("tick")
                self.send_events()

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=ticker, daemon=True)
        self._worker_thread.start()
        logger.success("started")

    def stop(self):
        logger.info("Shutting down")
        self._stop_event.set()

    def hello_world(self, name: str):
        logger.info(f"hello world: {name}")
        return {
            "status": "success",
            "content": f"hello world, name passed in was {name}"
        }

    def send_events(self):
        event_system.publish(
            EventMessage(
                role="tool",
                name="hello_world",
                content={
                    "message": "Hello from a plugin! this is a self-test of the plug-in system."
                },
                process_output=True
            )
        )
        self._stop_event.set()
