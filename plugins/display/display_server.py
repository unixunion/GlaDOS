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

        @self._flask_app.route("/shopping")
        def shopping_mobile():
            return render_template("shopping_mobile.html")

        def _load_spotify_env():
            """Load Spotify credentials from .env if not already in environment."""
            if not os.environ.get("SPOTIFY_CLIENT_ID"):
                env_path = os.path.join(os.getcwd(), ".env")
                if os.path.exists(env_path):
                    with open(env_path) as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                key, _, value = line.partition("=")
                                os.environ[key.strip()] = value.strip().strip('"').strip("'")

        @self._flask_app.route("/spotify/auth")
        def spotify_auth():
            """Start Spotify OAuth flow — redirects to Spotify login."""
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyOAuth
            except ImportError:
                return "spotipy not installed. Run: pip install spotipy", 500

            _load_spotify_env()
            client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
            client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                return ("<h2>Spotify credentials not set</h2>"
                        "<p>Set <code>SPOTIFY_CLIENT_ID</code> and <code>SPOTIFY_CLIENT_SECRET</code> "
                        "environment variables (or in <code>.env</code>).</p>"
                        "<p>Get them from <a href='https://developer.spotify.com/dashboard'>Spotify Developer Dashboard</a>.</p>"
                        "<p>Add redirect URI: <code>http://127.0.0.1:5001/spotify/callback</code></p>"), 400

            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5001/spotify/callback"),
                scope="user-modify-playback-state user-read-playback-state user-read-currently-playing",
                cache_path=os.path.join(os.getcwd(), ".spotify_cache"),
                open_browser=False,
            )
            auth_url = auth_manager.get_authorize_url()
            from flask import redirect
            return redirect(auth_url)

        @self._flask_app.route("/spotify/callback")
        def spotify_callback():
            """Handle Spotify OAuth callback — saves token and shows result."""
            from flask import request
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyOAuth
            except ImportError:
                return "spotipy not installed", 500

            _load_spotify_env()
            code = request.args.get("code")
            if not code:
                return "<h2>Error</h2><p>No authorization code received.</p>", 400

            auth_manager = SpotifyOAuth(
                client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
                client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
                redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5001/spotify/callback"),
                scope="user-modify-playback-state user-read-playback-state user-read-currently-playing",
                cache_path=os.path.join(os.getcwd(), ".spotify_cache"),
                open_browser=False,
            )
            try:
                auth_manager.get_access_token(code)
                sp = spotipy.Spotify(auth_manager=auth_manager)
                devices = sp.devices().get("devices", [])
                device_html = "".join(
                    f"<li>{d['name']} ({d['type']}) {'<b>[ACTIVE]</b>' if d['is_active'] else ''}</li>"
                    for d in devices
                ) or "<li>No devices found — open Spotify on a device</li>"

                logger.success("[Spotify] OAuth token saved successfully")
                return (f"<h2>Spotify Connected!</h2>"
                        f"<p>Token saved. Restart GlaDOS or the music plugin will pick it up.</p>"
                        f"<h3>Devices</h3><ul>{device_html}</ul>"
                        f"<p><a href='/'>Back to GlaDOS</a></p>")
            except Exception as e:
                logger.error(f"[Spotify] OAuth callback failed: {e}")
                return f"<h2>Authentication Failed</h2><p>{e}</p><p><a href='/spotify/auth'>Try Again</a></p>", 500

        @self._flask_app.route("/images/<path:filename>")
        def serve_image(filename):
            images_dir = os.path.join(os.getcwd(), "glados_ui", "images")
            logger.debug(f"Serving image from: {images_dir}/{filename}")
            return send_from_directory(images_dir, filename)

        @self._flask_app.route("/recipe-images/<path:filename>")
        def serve_recipe_image(filename):
            images_dir = os.path.join(os.getcwd(), "data", "recipes", "img", "Food Images")
            return send_from_directory(images_dir, filename)

        @self._flask_app.route("/plugin-views/<path:filepath>")
        def serve_plugin_view(filepath):
            """Serve plugin view JS/CSS files from their plugin directories."""
            # Sanitize path
            filepath = filepath.replace("..", "")
            full_path = os.path.join(os.getcwd(), filepath)
            if not os.path.exists(full_path):
                return "Not found", 404
            directory = os.path.dirname(full_path)
            filename = os.path.basename(full_path)
            return send_from_directory(directory, filename)

        @self._flask_app.route("/api/views")
        def api_views():
            """Return registered plugin views for the frontend to load."""
            from flask import jsonify
            views = plugin_manager.get_views()
            result = []
            for view_type, info in views.items():
                entry = {"view_type": view_type, "js_url": f"/plugin-views/{info['js_path']}"}
                if info.get("css_path"):
                    entry["css_url"] = f"/plugin-views/{info['css_path']}"
                entry["dashboard_card"] = info.get("dashboard_card", False)
                result.append(entry)
            return jsonify(result)

        @self._flask_app.route("/wiki")
        @self._flask_app.route("/wiki/")
        @self._flask_app.route("/wiki/<page>")
        def wiki(page="index"):
            wiki_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs", "wiki")
            # Sanitize page name and strip .md extension (allows both /wiki/arch and /wiki/arch.md)
            page = page.replace("..", "").replace("/", "").replace("\\", "")
            if page.endswith(".md"):
                page = page[:-3]
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
                                slug = m.group(2)
                                if slug.endswith(".md"):
                                    slug = slug[:-3]
                                sidebar_pages.append({"title": m.group(1), "slug": slug})

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

        @self._socketio.on("system_control")
        def handle_system_control(data):
            action = data.get("action", "")
            logger.info(f"[Display] System control: {action}")
            self.event_system.publish(EventMessage("system", action, {}))

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

        # Plugin UI actions are registered dynamically in start()
        # after all plugins have loaded and registered their actions

    def _on_display_event(self, event: EventMessage):
        """Handle display.* events and push to all connected browsers."""
        if isinstance(event.content, dict):
            payload = {"view_type": event.name, **event.content}
        else:
            payload = {"view_type": event.name, "title": "", "content": str(event.content)}
        # Don't cache data-only responses as the "current display" —
        # these are replies to get_state/get_summary requests, not user-initiated views
        if event.name not in ("dashboard_data", "shopping_list", "pantry"):
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

        # NLP handler for display commands (works without LLM)
        import re as _re
        from glados.nlp.handler import NLPHandler, NLPHandlerRegistry

        def _display_extract(text: str) -> dict:
            text_lower = text.lower()
            if any(w in text_lower for w in ["clear", "reset", "blank"]):
                return {"view_type": "clear"}
            if any(w in text_lower for w in ["recipe", "ingredient", "cooking"]):
                return {"view_type": "recipe"}
            if any(w in text_lower for w in ["timer", "countdown", "alarm"]):
                return {"view_type": "timer"}
            return {"view_type": "info", "content": text}

        NLPHandlerRegistry().register(NLPHandler(
            tool_name="show_on_display",
            extract_fn=_display_extract,
            response_fn=lambda r: r.get("message", "Done.") if isinstance(r, dict) else "Done.",
        ))

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

        # Register built-in views
        plugin_manager.register_view("info", "plugins/display/views/info.js")
        plugin_manager.register_view("settings", "plugins/display/views/settings.js",
                                     css_path="plugins/display/views/settings.css",
                                     dashboard_card=True)

        # Settings action handler
        plugin_manager.register_ui_action("settings_action", self._on_settings_action)

        # Register known plugin UI actions as SocketIO event handlers
        ui_actions = plugin_manager.get_ui_actions()
        registered_actions = set()
        for action_name in ui_actions:
            def make_handler(name):
                @self._socketio.on(name)
                def handler(data):
                    logger.info(f"[Display] Plugin UI action: {name}: {data}")
                    self.event_system.publish(EventMessage("ui", name, data))
            make_handler(action_name)
            registered_actions.add(action_name)
            logger.info(f"[Display] Registered SocketIO handler for plugin UI action: {action_name}")

        # Late-registered actions: plugins that register UI actions in start()
        # (after the display server loop above) need a way to get wired up.
        # Re-check periodically and register any new ones.
        def _wire_late_actions(event):
            for name in plugin_manager.get_ui_actions():
                if name not in registered_actions:
                    def make_late_handler(n):
                        @self._socketio.on(n)
                        def handler(data):
                            logger.info(f"[Display] Plugin UI action: {n}: {data}")
                            self.event_system.publish(EventMessage("ui", n, data))
                    make_late_handler(name)
                    registered_actions.add(name)
                    logger.info(f"[Display] Late-registered SocketIO handler: {name}")
        self.event_system.subscribe(
            "system.tick",
            EventHook("display_late_actions", callback=_wire_late_actions, priority=99)
        )

        # Subscribe to settings UI action
        self.event_system.subscribe(
            "ui.settings_action",
            EventHook("settings_ui", callback=self._on_settings_action, priority=5)
        )

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
            from glados.config import GladosConfig
            try:
                config = GladosConfig.from_yaml("glados_config.yml")
                use_ssl = getattr(config, 'display_ssl', False)
            except Exception:
                use_ssl = False

            if use_ssl:
                logger.info("Starting display server on port 5001 (HTTPS)...")
                import ssl
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                cert_path = os.path.join(os.path.dirname(__file__), "cert.pem")
                key_path = os.path.join(os.path.dirname(__file__), "key.pem")
                if not os.path.exists(cert_path):
                    logger.info("Generating self-signed SSL certificate...")
                    try:
                        import subprocess
                        subprocess.run([
                            "openssl", "req", "-x509", "-newkey", "rsa:2048",
                            "-keyout", key_path, "-out", cert_path,
                            "-days", "3650", "-nodes",
                            "-subj", "/CN=GlaDOS/O=Aperture Science",
                            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:0.0.0.0",
                        ], check=True, capture_output=True)
                    except Exception as e:
                        logger.warning(f"Could not generate SSL cert: {e}. Falling back to HTTP.")
                        use_ssl = False
                if use_ssl:
                    ctx.load_cert_chain(cert_path, key_path)
                    self._socketio.run(self._flask_app, host="0.0.0.0", port=5001, allow_unsafe_werkzeug=True, ssl_context=ctx)
                    return

            logger.info("Starting display server on port 5001 (HTTP)...")
            self._socketio.run(self._flask_app, host="0.0.0.0", port=5001, allow_unsafe_werkzeug=True)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=run_flask, daemon=True)
        self._worker_thread.start()
        logger.success("DisplayPlugin started on port 5001!")

    def _on_settings_action(self, event):
        """Handle settings UI actions — gather system info and push to display."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")

        if action == "show":
            from glados.config import GladosConfig
            try:
                config = GladosConfig.from_yaml("glados_config.yml")
            except Exception:
                config = None

            # Check Spotify status
            spotify_info = {"connected": False}
            try:
                import spotipy
                cache_path = os.path.join(os.getcwd(), ".spotify_cache")
                if os.path.exists(cache_path):
                    from spotipy.oauth2 import SpotifyOAuth
                    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                        client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
                        client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
                        redirect_uri="http://127.0.0.1:5001/spotify/callback",
                        scope="user-read-playback-state",
                        cache_path=cache_path,
                        open_browser=False,
                    ))
                    playback = sp.current_playback()
                    spotify_info["connected"] = True
                    if playback and playback.get("device"):
                        spotify_info["device"] = playback["device"]["name"]
            except Exception:
                pass

            # System info
            system_info = {}
            if config:
                system_info = {
                    "model": getattr(config, "model", ""),
                    "client_type": getattr(config, "client_type", ""),
                    "nlp_mode": getattr(config, "nlp_mode", False),
                    "voice_core": getattr(config, "voice_core", ""),
                    "knowledge_enabled": getattr(config, "knowledge_enabled", False),
                    "memory_enabled": getattr(config, "memory_enabled", False),
                    "tts_fade_ms": getattr(config, "tts_fade_ms", 10),
                    "plugin_count": len(plugin_manager.plugins),
                }
                # Knowledge point count
                if system_info["knowledge_enabled"]:
                    try:
                        from qdrant_client import QdrantClient
                        qc = QdrantClient(url=getattr(config, "qdrant_url", "http://localhost:6333"), timeout=2)
                        for coll in (getattr(config, "knowledge_collections", None) or []):
                            info = qc.get_collection(coll)
                            system_info["knowledge_points"] = info.points_count
                    except Exception:
                        system_info["knowledge_points"] = "?"

            self.event_system.publish(EventMessage(
                role="display", name="settings",
                content={"spotify": spotify_info, "system": system_info},
                process_output=False,
            ))

    def stop(self):
        logger.info("Stopping DisplayPlugin...")
        self._stop_event.set()
        logger.success("DisplayPlugin stopped!")
