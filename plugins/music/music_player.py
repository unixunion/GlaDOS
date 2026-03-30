import os
import re
import threading
from typing import Optional

from loguru import logger
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.nlp.extractors import extract_music_action
from glados.system.event_system import EventHook, EventMessage


def _music_nlp_extract(text: str) -> dict:
    """Extract music action and query from natural language."""
    action, query = extract_music_action(text)
    params = {"action": action}
    if query:
        params["query"] = query
    return params


def _music_nlp_response(result: dict) -> str:
    status = result.get("status", "")
    if status == "error":
        return result.get("message", "Music playback error.")
    if status == "playing":
        track = result.get("track", "")
        artist = result.get("artist", "")
        playlist = result.get("playlist", "")
        if track and artist:
            return f"Now playing {track} by {artist}."
        if artist:
            return f"Now playing {artist}."
        if playlist:
            return f"Now playing playlist {playlist}."
        return "Playing now."
    if status in ("paused", "stopped"):
        return "Music paused."
    if status == "resumed":
        return "Resuming playback."
    if status == "skipped":
        return "Skipped to next track."
    if status == "previous":
        return "Playing previous track."
    return result.get("message", "Done.")


def _list_devices_nlp_response(result: dict) -> str:
    if result.get("status") == "error":
        return result.get("message", "Couldn't list devices.")
    devices = result.get("devices", [])
    if not devices:
        return "No Spotify devices found."
    parts = []
    for d in devices:
        label = f"{d.get('number', '?')}, {d['name']}"
        if d.get("active"):
            label += " (active)"
        parts.append(label)
    return "Available devices: " + ". ".join(parts) + ". Say 'play on' followed by the name or number to switch."


def _now_playing_nlp_response(result: dict) -> str:
    if result.get("status") == "idle":
        return "No music is currently playing."
    if result.get("status") == "error":
        return result.get("message", "Couldn't check what's playing.")
    track = result.get("track", "Unknown")
    artist = result.get("artist", "Unknown")
    status = "playing" if result.get("status") == "playing" else "paused"
    return f"Currently {status}: {track} by {artist}."

# Spotify OAuth scopes needed for playback control
SPOTIFY_SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"


class MusicPlayer(RunnableMCPPlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(MusicPlayer, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()

        self._lock = threading.Lock()
        self._paused_by_alarm = False

        # Spotify client — uses cached token from spotify_auth.py
        self.sp: Optional[spotipy.Spotify] = None
        cache_path = os.path.join(os.getcwd(), ".spotify_cache")

        # Load .env file if env vars aren't already set
        if not os.environ.get("SPOTIFY_CLIENT_ID"):
            env_path = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            os.environ[key.strip()] = value.strip().strip('"').strip("'")
                logger.info("Loaded Spotify credentials from .env")

        if os.path.exists(cache_path):
            try:
                self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                    client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
                    client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
                    redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5001/spotify/callback"),
                    scope=SPOTIFY_SCOPES,
                    cache_path=cache_path,
                    open_browser=False,
                ))
                self.sp.current_playback()
                logger.success("Spotify client authenticated from cached token.")
            except Exception as e:
                logger.warning(f"Spotify authentication failed: {e}. Run `python3 spotify_auth.py` to authenticate.")
                self.sp = None
        else:
            logger.warning("No Spotify cache found. Run `python3 spotify_auth.py` to authenticate.")

        # Register tools
        self.register_tool(
            handler=self.play_music,
            description=(
                "Controls Spotify music playback. "
                "PLAY: search and play a song, artist, album, or playlist. "
                "PAUSE: pause playback. RESUME: resume playback. "
                "STOP: stop playback. SKIP: next track. PREVIOUS: previous track."
            ),
            parameters={
                "action": {
                    "type": "string",
                    "description": "The playback action.",
                    "enum": ["PLAY", "PAUSE", "RESUME", "STOP", "SKIP", "PREVIOUS"],
                },
                "query": {
                    "type": "string",
                    "description": "What to play. Required for PLAY. Can be a song, artist, album, or playlist name.",
                },
            },
            required=["action"],
            intents=[
                "play some music",
                "play ben howard",
                "play the album spice",
                "play sugar by maroon 5",
                "play bohemian rhapsody",
                "play something by taylor swift",
                "place sugar by maroon five",
                "stop playing",
                "stop the music",
                "play the song teen spirit by nirvana",
                "play my chemical romance",
                "play the playlist chill vibes",
                "put on some jazz",
                "next track",
                "skip song",
                "previous track",
                "pause the music",
                "pause",
                "resume the music",
            ],
            process_output=True,
            activity=[Activity.ENTERTAINMENT, Activity.GENERAL],
            nlp_extract_fn=_music_nlp_extract,
            nlp_response=_music_nlp_response,
        )

        self.register_tool(
            handler=self.now_playing,
            description="Gets the currently playing track from Spotify.",
            intents=[
                "what song is playing",
                "what is this song",
                "what track is on",
                "name this song",
                "what's playing",
                "what song is this",
                "tell me the song name",
                "what am I listening to",
                "what music is on",
                "which song is this",
                "what's currently playing",
            ],
            process_output=True,
            activity=[Activity.ENTERTAINMENT, Activity.GENERAL],
            nlp_response=_now_playing_nlp_response,
        )

        self.register_tool(
            handler=self.list_devices,
            description="Lists available Spotify playback devices.",
            intents=[
                "list spotify devices",
                "what devices are available",
                "which speaker is active",
                "show me my speakers",
                "list music devices",
                "what speakers are connected",
                "show available devices",
                "which devices can play music",
                "list playback devices",
                "where can I play music",
            ],
            process_output=True,
            activity=[Activity.ENTERTAINMENT, Activity.GENERAL],
            nlp_response=_list_devices_nlp_response,
        )

        self.register_tool(
            handler=self.switch_device,
            description="Switch Spotify playback to a different device by name or number.",
            parameters={
                "device": {
                    "type": "string",
                    "description": "Device name (fuzzy matched) or number from list_devices",
                },
            },
            required=["device"],
            intents=[
                "play on the kitchen speaker",
                "switch to the living room",
                "play music on device 2",
                "transfer playback to the TV",
                "use the bedroom speaker",
                "switch audio to my phone",
                "play on speaker number 1",
            ],
            process_output=True,
            activity=[Activity.ENTERTAINMENT, Activity.GENERAL],
            nlp_extract_fn=lambda text: {"device": re.sub(
                r"^(?:play\s+(?:music\s+)?on\s+(?:the\s+)?|switch\s+(?:to|audio\s+to)\s+(?:the\s+)?|"
                r"transfer\s+(?:playback\s+)?to\s+(?:the\s+)?|use\s+(?:the\s+)?)",
                "", text, flags=re.IGNORECASE
            ).strip() or text},
            nlp_response=lambda r: r.get("message", "Done."),
        )

    @property
    def is_playing(self) -> bool:
        if not self.sp:
            return False
        try:
            pb = self.sp.current_playback()
            return pb is not None and pb.get("is_playing", False)
        except Exception:
            return False

    def start(self):
        logger.info("MusicPlayer (Spotify) started.")
        self.event_system.subscribe(
            "system.music_pause",
            EventHook("music_pause", callback=self._on_pause_event, priority=5)
        )
        self.event_system.subscribe(
            "system.music_resume",
            EventHook("music_resume", callback=self._on_resume_event, priority=5)
        )

        # Register display view and UI action
        self.register_view("music", "plugins/music/views/music.js", dashboard_card=True)
        self.register_ui_action("music_action", self._on_music_ui_action)
        self.event_system.subscribe(
            "ui.music_action",
            EventHook("music_ui", callback=self._on_music_ui_action, priority=5)
        )
        # Poll Spotify state every ~10 seconds to catch track changes
        self._tick_count = 0
        self.event_system.subscribe(
            "system.tick",
            EventHook("music_poll", callback=self._on_tick, priority=10)
        )

    def _on_tick(self, event):
        """Poll Spotify every ~10 ticks (seconds) for track changes."""
        self._tick_count += 1
        if self._tick_count == 3 or self._tick_count % 10 == 0:
            self._publish_music_state()

    def _publish_music_state(self):
        """Push current playback state to dashboard."""
        state = {"is_playing": False, "track": "", "artist": "", "device": ""}
        if self.sp:
            try:
                pb = self.sp.current_playback()
                if pb:
                    state["is_playing"] = pb.get("is_playing", False)
                    item = pb.get("item")
                    if item:
                        state["track"] = item.get("name", "")
                        artists = item.get("artists", [])
                        state["artist"] = artists[0]["name"] if artists else ""
                    dev = pb.get("device")
                    if dev:
                        state["device"] = dev.get("name", "")
            except Exception:
                pass
        self.event_system.publish(EventMessage(
            role="display", name="dashboard_data",
            content={"music": state},
            process_output=False,
        ))

    def _on_music_ui_action(self, event):
        """Handle music UI actions from the display."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action", "").upper()

        if action == "GET_STATE":
            self._publish_music_state()
            return
        elif action == "SWITCH_DEVICE":
            device = data.get("device", "")
            if device:
                self.switch_device(device)
        elif action in ("PLAY", "PAUSE", "RESUME", "STOP", "SKIP", "PREVIOUS"):
            self.play_music(action=action)

        # Update dashboard state after action
        self._publish_music_state()

    def stop(self):
        logger.info("MusicPlayer stopping...")

    def _on_pause_event(self, event):
        if self.is_playing:
            logger.info("Spotify paused by system event (alarm).")
            self._paused_by_alarm = True
            try:
                self.sp.pause_playback()
            except Exception as e:
                logger.warning(f"Failed to pause Spotify: {e}")

    def _on_resume_event(self, event):
        if self._paused_by_alarm:
            logger.info("Spotify resumed after alarm dismissed.")
            self._paused_by_alarm = False
            try:
                self.sp.start_playback()
            except Exception as e:
                logger.warning(f"Failed to resume Spotify: {e}")

    def _get_active_device(self) -> Optional[str]:
        try:
            devices = self.sp.devices()
            if not devices or not devices.get("devices"):
                return None
            for d in devices["devices"]:
                if d.get("is_active"):
                    return d["id"]
            return devices["devices"][0]["id"]
        except Exception as e:
            logger.warning(f"Could not get Spotify devices: {e}")
            return None

    def _search_and_play(self, query: str) -> dict:
        try:
            results = self.sp.search(q=query, type="track", limit=5)
            tracks = results.get("tracks", {}).get("items", [])

            if tracks:
                track = tracks[0]
                track_name = track["name"]
                artist = track["artists"][0]["name"]
                uri = track["uri"]
                device_id = self._get_active_device()
                if not device_id:
                    return {"status": "error", "message": "No active Spotify device found. Open Spotify on a device first."}
                self.sp.start_playback(device_id=device_id, uris=[uri])
                return {"status": "playing", "track": track_name, "artist": artist}

            results = self.sp.search(q=query, type="artist", limit=3)
            artists = results.get("artists", {}).get("items", [])
            if artists:
                artist = artists[0]
                device_id = self._get_active_device()
                if not device_id:
                    return {"status": "error", "message": "No active Spotify device found. Open Spotify on a device first."}
                self.sp.start_playback(device_id=device_id, context_uri=artist["uri"])
                return {"status": "playing", "artist": artist["name"]}

            results = self.sp.search(q=query, type="playlist", limit=3)
            playlists = results.get("playlists", {}).get("items", [])
            if playlists:
                playlist = playlists[0]
                device_id = self._get_active_device()
                if not device_id:
                    return {"status": "error", "message": "No active Spotify device found. Open Spotify on a device first."}
                self.sp.start_playback(device_id=device_id, context_uri=playlist["uri"])
                return {"status": "playing", "playlist": playlist["name"]}

            return {"status": "error", "message": f"No results found for '{query}'."}

        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error: {e}")
            return {"status": "error", "message": str(e.msg) if hasattr(e, 'msg') else str(e)}
        except Exception as e:
            logger.error(f"Spotify search error: {e}")
            return {"status": "error", "message": str(e)}

    def play_music(self, action: str, query: str = None) -> dict:
        if not self.sp:
            return {"status": "error", "message": "Spotify is not configured. Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and SPOTIFY_REDIRECT_URI."}

        action = action.upper()
        try:
            if action == "PLAY":
                if not query:
                    device_id = self._get_active_device()
                    if device_id:
                        self.sp.start_playback(device_id=device_id)
                        return {"status": "resumed"}
                    return {"status": "error", "message": "Please specify what to play."}
                return self._search_and_play(query)
            elif action == "PAUSE":
                self.sp.pause_playback()
                return {"status": "paused"}
            elif action == "RESUME":
                device_id = self._get_active_device()
                if device_id:
                    self.sp.start_playback(device_id=device_id)
                    return {"status": "resumed"}
                return {"status": "error", "message": "No active Spotify device found."}
            elif action == "STOP":
                self.sp.pause_playback()
                return {"status": "stopped"}
            elif action == "SKIP":
                self.sp.next_track()
                return {"status": "skipped"}
            elif action == "PREVIOUS":
                self.sp.previous_track()
                return {"status": "previous"}
            else:
                return {"status": "error", "message": f"Unknown action: {action}"}
        except spotipy.SpotifyException as e:
            return {"status": "error", "message": str(e.msg) if hasattr(e, 'msg') else str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def now_playing(self) -> dict:
        if not self.sp:
            return {"status": "error", "message": "Spotify is not configured."}
        try:
            pb = self.sp.current_playback()
            if not pb or not pb.get("item"):
                return {"status": "idle", "message": "No music is currently playing."}
            track = pb["item"]
            artist = ", ".join(a["name"] for a in track.get("artists", []))
            return {
                "status": "playing" if pb.get("is_playing") else "paused",
                "track": track.get("name", "Unknown"),
                "artist": artist,
                "album": track.get("album", {}).get("name", ""),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def switch_device(self, device: str) -> dict:
        """Switch Spotify playback to a device by name (fuzzy) or number."""
        if not self.sp:
            return {"status": "error", "message": "Spotify is not configured."}
        try:
            from rapidfuzz import fuzz
            result = self.sp.devices()
            if not result or not result.get("devices"):
                return {"status": "error", "message": "No Spotify devices found."}

            devices = result["devices"]
            target = None

            # Try numeric match: "device 2", "number 1", just "2"
            num_match = re.search(r"(\d+)", device)
            if num_match:
                idx = int(num_match.group(1)) - 1  # 1-indexed
                if 0 <= idx < len(devices):
                    target = devices[idx]

            # Fuzzy name match
            if not target:
                best_score, best_device = 0, None
                for d in devices:
                    score = fuzz.partial_ratio(device.lower(), d["name"].lower())
                    if score > best_score:
                        best_score = score
                        best_device = d
                if best_score >= 60:
                    target = best_device

            if not target:
                names = ", ".join(d["name"] for d in devices)
                return {"status": "error", "message": f"No device matching '{device}'. Available: {names}"}

            self.sp.transfer_playback(target["id"], force_play=True)
            logger.info(f"[Music] Switched playback to {target['name']}")
            return {"status": "success", "message": f"Switched playback to {target['name']}."}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def list_devices(self) -> dict:
        """List available Spotify playback devices."""
        if not self.sp:
            return {"status": "error", "message": "Spotify is not configured."}
        try:
            result = self.sp.devices()
            if not result or not result.get("devices"):
                return {"status": "error", "message": "No Spotify devices found. Open Spotify on a device first."}
            devices = [
                {
                    "id": d.get("id", ""),
                    "name": d.get("name", "Unknown"),
                    "type": d.get("type", "Unknown"),
                    "active": d.get("is_active", False),
                    "number": i + 1,
                }
                for i, d in enumerate(result["devices"])
            ]
            return {"status": "success", "devices": devices}
        except Exception as e:
            return {"status": "error", "message": str(e)}
