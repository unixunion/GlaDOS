import threading
import time

from loguru import logger

from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()  # the plugin manager
event_system = EventSystem()  # allows us to send stuff to the LLM whenever we want


class MyRunnablePlugin(RunnablePlugin):
    def __init__(self):
        return  # intentionally disabled for testing
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

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                function=FunctionMetadata(
                    description="Hello Universe, greets the universe",
                    parameters=Parameters(type="object", required=[], properties={
                    })
                )),
            intents=[
                "run the hello universe plugin",
                "hello universe",
                "invoke the hello universe function"
            ],
            process_output=True  # process output via llm model inference,
        )(self.hello_universe)

    def start(self):
        # __init__ short-circuits with an intentional `return` for testing,
        # so _stop_event may not exist. Skip start in that case.
        if not hasattr(self, "_stop_event"):
            return
        logger.info("Starting...")
        if getattr(self, '_worker_thread', None) and self._worker_thread.is_alive():
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
        if not hasattr(self, "_stop_event"):
            return
        logger.info("Shutting down")
        self._stop_event.set()

    def hello_world(self, name: str):
        logger.info(f"hello world: {name}")
        return {
            "status": "success",
            "content": f"hello world, name passed in was {name}"
        }

    def hello_universe(self):
        logger.info("testing crash method")
        raise ValueError("error parsing the input, the user has provided bad data")

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
