import os
import threading
from typing import Optional

from loguru import logger
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from glados.context.activity import Activity
from glados.system.event_system import EventSystem, EventHook
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()

# Spotify OAuth scopes needed for playback control
SPOTIFY_SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"


class MusicPlayer(RunnablePlugin):
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
        self._paused_by_alarm = False  # Track if we paused due to an alarm

        # Spotify client — uses cached token from spotify_auth.py
        # Run `python3 spotify_auth.py` first to authenticate
        self.sp: Optional[spotipy.Spotify] = None
        cache_path = os.path.join(os.getcwd(), ".spotify_cache")

        # Load .env file if env vars aren't already set
        if not os.environ.get("SPOTIPY_CLIENT_ID"):
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
                    client_id=os.environ.get("SPOTIPY_CLIENT_ID", ""),
                    client_secret=os.environ.get("SPOTIPY_CLIENT_SECRET", ""),
                    redirect_uri=os.environ.get("SPOTIPY_REDIRECT_URI", "https://127.0.0.1:8888/callback"),
                    scope=SPOTIFY_SCOPES,
                    cache_path=cache_path,
                    open_browser=False,
                ))
                # Test that the token works
                self.sp.current_playback()
                logger.success("Spotify client authenticated from cached token.")
            except Exception as e:
                logger.warning(f"Spotify authentication failed: {e}. Run `python3 spotify_auth.py` to authenticate.")
                self.sp = None
        else:
            logger.warning("No Spotify cache found. Run `python3 spotify_auth.py` to authenticate.")

        # Register tools
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description=(
                        "Controls Spotify music playback. "
                        "PLAY: search and play a song, artist, album, or playlist. "
                        "PAUSE: pause playback. RESUME: resume playback. "
                        "STOP: stop playback. SKIP: next track. PREVIOUS: previous track."
                    ),
                    parameters=Parameters(
                        type="object",
                        properties={
                            "action": ParameterType(
                                type="string",
                                description="The playback action.",
                                enum=["PLAY", "PAUSE", "RESUME", "STOP", "SKIP", "PREVIOUS"],
                            ),
                            "query": ParameterType(
                                type="string",
                                description="What to play. Required for PLAY. Can be a song, artist, album, or playlist name.",
                            ),
                        },
                        required=["action"],
                        additionalProperties=False,
                    ),
                ),
            ),
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
        )(self.play_music)

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Gets the currently playing track from Spotify.",
                    parameters=Parameters(type="object", properties={}, required=[], additionalProperties=False),
                ),
            ),
            intents=[
                "what song is playing",
                "what is this song",
                "what track is on",
            ],
            process_output=True,
            activity=[Activity.ENTERTAINMENT, Activity.GENERAL],
        )(self.now_playing)

    @property
    def is_playing(self) -> bool:
        """Check if Spotify is currently playing."""
        if not self.sp:
            return False
        try:
            pb = self.sp.current_playback()
            return pb is not None and pb.get("is_playing", False)
        except Exception:
            return False

    def start(self):
        logger.info("MusicPlayer (Spotify) started.")
        self.event_system = EventSystem()
        self.event_system.subscribe(
            "system.music_pause",
            EventHook("music_pause", callback=self._on_pause_event, priority=5)
        )
        self.event_system.subscribe(
            "system.music_resume",
            EventHook("music_resume", callback=self._on_resume_event, priority=5)
        )

    def stop(self):
        logger.info("MusicPlayer stopping...")

    def _on_pause_event(self, event):
        """Pause Spotify in response to a system event (e.g. alarm firing)."""
        if self.is_playing:
            logger.info("Spotify paused by system event (alarm).")
            self._paused_by_alarm = True
            try:
                self.sp.pause_playback()
            except Exception as e:
                logger.warning(f"Failed to pause Spotify: {e}")

    def _on_resume_event(self, event):
        """Resume Spotify in response to a system event (e.g. alarm dismissed)."""
        if self._paused_by_alarm:
            logger.info("Spotify resumed after alarm dismissed.")
            self._paused_by_alarm = False
            try:
                self.sp.start_playback()
            except Exception as e:
                logger.warning(f"Failed to resume Spotify: {e}")

    def _get_active_device(self) -> Optional[str]:
        """Find an active Spotify device, or the first available one."""
        try:
            devices = self.sp.devices()
            if not devices or not devices.get("devices"):
                return None
            # Prefer the active device
            for d in devices["devices"]:
                if d.get("is_active"):
                    return d["id"]
            # Fall back to first available
            return devices["devices"][0]["id"]
        except Exception as e:
            logger.warning(f"Could not get Spotify devices: {e}")
            return None

    def _search_and_play(self, query: str) -> dict:
        """Search Spotify and start playback of the best match."""
        try:
            # Try track search first
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

            # Try artist search
            results = self.sp.search(q=query, type="artist", limit=3)
            artists = results.get("artists", {}).get("items", [])
            if artists:
                artist = artists[0]
                device_id = self._get_active_device()
                if not device_id:
                    return {"status": "error", "message": "No active Spotify device found. Open Spotify on a device first."}
                self.sp.start_playback(device_id=device_id, context_uri=artist["uri"])
                return {"status": "playing", "artist": artist["name"]}

            # Try playlist search
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
        """LLM-callable tool to control Spotify playback."""
        if not self.sp:
            return {"status": "error", "message": "Spotify is not configured. Set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI."}

        action = action.upper()

        try:
            if action == "PLAY":
                if not query:
                    # Resume current playback if no query
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
        """Return the current Spotify playback status."""
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
