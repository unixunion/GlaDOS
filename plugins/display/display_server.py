import os
import threading

from flask import Flask, render_template, send_from_directory
from flask_socketio import SocketIO
from loguru import logger

from glados.context.activity import Activity
from glados.system.event_system import EventSystem, EventMessage, EventHook
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()
event_system = EventSystem()


class DisplayPlugin(RunnablePlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DisplayPlugin, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._flask_app = Flask(__name__, template_folder="templates", static_folder="static")
        self._socketio = SocketIO(self._flask_app, cors_allowed_origins="*")
        self.current_display = {"view_type": "idle", "title": "", "content": ""}
        self.event_system = EventSystem()
        self._configure_routes()
        self._register_tools()

    def _configure_routes(self):
        @self._flask_app.route("/")
        def index():
            return render_template("display.html")

        @self._flask_app.route("/images/<path:filename>")
        def serve_image(filename):
            images_dir = os.path.join(os.getcwd(), "glados_ui", "images")
            logger.debug(f"Serving image from: {images_dir}/{filename}")
            return send_from_directory(images_dir, filename)

        @self._socketio.on("connect")
        def handle_connect():
            logger.info("Display client connected")
            self._socketio.emit("display_update", self.current_display)

    def _on_display_event(self, event: EventMessage):
        """Handle display.* events and push to all connected browsers."""
        logger.info(f"Display event received: {event.name}")
        if isinstance(event.content, dict):
            payload = {"view_type": event.name, **event.content}
        else:
            payload = {"view_type": event.name, "title": "", "content": str(event.content)}
        self.current_display = payload
        self._socketio.emit("display_update", payload)

    def _on_status_event(self, event: EventMessage):
        """Handle status.* events and push toast notifications to all connected browsers."""
        logger.debug(f"Status event received: {event.name}")
        if isinstance(event.content, dict):
            payload = {"status": event.name, **event.content}
        else:
            payload = {"status": event.name, "message": str(event.content)}
        self._socketio.emit("status_toast", payload)

    def _on_tick(self, event: EventMessage):
        """On each tick, if displaying a timer view, push live countdown state."""
        if self.current_display.get("view_type") != "timer":
            return
        try:
            from plugins.basic.countdown_timer import CountdownTimer
            timer_data = CountdownTimer().list_timers()
            if timer_data.get("status") == "success":
                payload = {
                    "view_type": "timer",
                    "title": "Active Timers",
                    "timers": timer_data["timers"]
                }
                self._socketio.emit("display_update", payload)
        except Exception as e:
            logger.debug(f"Could not update timer display: {e}")

    def _register_tools(self):
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Display content on the connected screen (iPad). Use this to show recipes, timers, lists, or any visual content.",
                    parameters=Parameters(
                        type="object",
                        properties={
                            "view_type": ParameterType(
                                type="string",
                                description="The type of content to display.",
                                enum=["recipe", "timer", "info", "clear"]
                            ),
                            "title": ParameterType(
                                type="string",
                                description="Title to display on screen."
                            ),
                            "content": ParameterType(
                                type="string",
                                description="The content to display. For recipes: ingredients and steps. For info: text or list. For timer: timer name. For clear: ignored."
                            ),
                        },
                        required=["view_type"],
                        additionalProperties=False,
                    ),
                ),
            ),
            intents=[
                "show me the recipe",
                "put ingredients on screen",
                "display the timer",
                "show that on the iPad",
                "clear the screen",
                "put that on the display",
                "show the recipe on screen",
            ],
            process_output=True,
            activity=[Activity.COOKING, Activity.GENERAL, Activity.UTILITIES, Activity.CHORES]
        )(self.show_on_display)

    def show_on_display(self, view_type: str, title: str = "", content: str = ""):
        """LLM-callable tool to push content to the display."""
        if view_type == "clear":
            view_type = "idle"
            title = ""
            content = ""

        self.event_system.publish(EventMessage(
            role="display",
            name=view_type,
            content={"title": title, "content": content},
            process_output=False
        ))

        return {"status": "displayed", "view_type": view_type, "title": title}

    def start(self):
        logger.info("Starting DisplayPlugin...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        # Subscribe to display events, status events, and tick
        self.event_system.subscribe(
            "display.*",
            EventHook("display_update", callback=self._on_display_event, priority=5)
        )
        self.event_system.subscribe(
            "status.*",
            EventHook("status_toast", callback=self._on_status_event, priority=5)
        )
        self.event_system.subscribe(
            "system.tick",
            EventHook("display_tick", callback=self._on_tick, priority=1)
        )

        def run_flask():
            logger.info("Starting display server on port 5001...")
            self._socketio.run(self._flask_app, host="0.0.0.0", port=5001, allow_unsafe_werkzeug=True)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=run_flask, daemon=True)
        self._worker_thread.start()
        logger.success("DisplayPlugin started on port 5001!")

    def stop(self):
        logger.info("Stopping DisplayPlugin...")
        self._stop_event.set()
        logger.success("DisplayPlugin stopped!")
