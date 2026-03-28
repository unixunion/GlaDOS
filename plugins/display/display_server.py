import os
import threading

import markdown
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

        plugin_manager.register_system_prompt(
            "A display screen is connected. Prefer showing lists, options, recipes, "
            "and any structured content on the display using the show_on_display tool "
            "rather than reading them aloud. Only read lists out loud if the user "
            "explicitly asks you to. When you use show_on_display, do NOT read or "
            "repeat that content aloud. Just briefly confirm it is displayed, e.g. "
            "'I've put that on the screen' or 'Here are your options on the display', "
            "and let the user read it themselves."
        )

    def _configure_routes(self):
        @self._flask_app.route("/")
        def index():
            return render_template("display.html")

        @self._flask_app.route("/images/<path:filename>")
        def serve_image(filename):
            images_dir = os.path.join(os.getcwd(), "glados_ui", "images")
            logger.debug(f"Serving image from: {images_dir}/{filename}")
            return send_from_directory(images_dir, filename)

        @self._flask_app.route("/wiki")
        @self._flask_app.route("/wiki/")
        @self._flask_app.route("/wiki/<page>")
        def wiki(page="index"):
            wiki_dir = os.path.join(os.path.dirname(__file__), "wiki")
            # Sanitize page name
            page = page.replace("..", "").replace("/", "").replace("\\", "")
            md_path = os.path.join(wiki_dir, f"{page}.md")

            if not os.path.exists(md_path):
                content_html = "<h1>Page Not Found</h1><p>This wiki page does not exist.</p>"
                title = "Not Found"
            else:
                with open(md_path, "r", encoding="utf-8") as f:
                    md_content = f.read()
                content_html = markdown.markdown(
                    md_content,
                    extensions=["tables", "fenced_code", "codehilite", "toc"],
                )
                # Extract title from first h1
                title = page.replace("_", " ").title()
                if md_content.startswith("# "):
                    title = md_content.split("\n")[0].lstrip("# ").strip()

            # Build sidebar from index.md links
            sidebar_pages = []
            index_path = os.path.join(wiki_dir, "index.md")
            if os.path.exists(index_path):
                with open(index_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("- ["):
                            # Parse "- [Title](slug)" format
                            import re
                            m = re.match(r"- \[(.+?)\]\((.+?)\)", line)
                            if m:
                                sidebar_pages.append({"title": m.group(1), "slug": m.group(2)})

            return render_template("wiki.html",
                                   title=title,
                                   content=content_html,
                                   sidebar_pages=sidebar_pages,
                                   current_page=page)

        @self._socketio.on("connect")
        def handle_connect():
            logger.info("Display client connected")
            self._socketio.emit("display_update", self.current_display)

        @self._socketio.on("interrupt")
        def handle_interrupt():
            logger.info("[Display] Interrupt requested from UI")
            self.event_system.publish(EventMessage(
                "system", "interrupt_tts", {}
            ))

        @self._socketio.on("user_message")
        def handle_user_message(data):
            text = data.get("text", "").strip()
            if text:
                logger.info(f"[Display] Chat input received: {text[:100]}")
                self.event_system.publish(EventMessage(
                    "status", "user_speech", {"message": text}
                ))
                self.event_system.publish(EventMessage(
                    "tool", "display_chat_input",
                    content=text,
                    process_output=True
                ))

        @self._socketio.on("shopping_list_action")
        def handle_shopping_list_action(data):
            logger.info(f"[Display] Shopping list action: {data}")
            self.event_system.publish(EventMessage(
                "ui", "shopping_list_action", data
            ))

        @self._socketio.on("pantry_action")
        def handle_pantry_action(data):
            logger.info(f"[Display] Pantry action: {data}")
            self.event_system.publish(EventMessage(
                "ui", "pantry_action", data
            ))

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

    def _on_chat_event(self, event: EventMessage):
        """Forward chat.* events to the frontend chat panel."""
        if isinstance(event.content, dict):
            self._socketio.emit("chat_message", event.content)
        else:
            self._socketio.emit("chat_message", {"role": event.name, "content": str(event.content)})

    def _on_tick(self, event: EventMessage):
        """Tick handler — timer display updates are now pushed by CountdownTimer directly."""
        pass

    def _register_tools(self):
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Display content on the connected screen/iPad/monitor. Use this to show recipes, active timers, lists, or any visual content. Also use this to clear or reset the screen. Call this for ANY request involving the display.",
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
                "clear the display",
                "reset the display",
                "put that on the display",
                "show the recipe on screen",
            ],
            process_output=True,
            activity=[Activity.COOKING, Activity.GENERAL, Activity.UTILITIES, Activity.CHORES, Activity.SYSTEM, Activity.ENTERTAINMENT]
        )(self.show_on_display)

    def show_on_display(self, view_type: str, title: str = "", content: str = ""):
        """LLM-callable tool to push content to the display."""
        if view_type == "clear":
            view_type = "idle"
            title = ""
            content = ""

        logger.info(f"Display update: view_type: {view_type}, title: {title}, content: {content}")
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

        # Chat panel events — forward to frontend as chat_message
        self.event_system.subscribe(
            "chat.*",
            EventHook("chat_to_display", callback=self._on_chat_event, priority=5)
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
