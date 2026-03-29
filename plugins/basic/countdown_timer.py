import dataclasses
import json
import os
from datetime import datetime, timedelta
from typing import Optional, List
from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.nlp.extractors import parse_duration, extract_after_keyword
from glados.system.event_system import EventMessage, EventHook


def format_duration(total_seconds: int) -> str:
    """
    Converts total seconds into a human-readable duration format for TTS.
    """
    if total_seconds < 60:
        return f"{total_seconds} second{'s' if total_seconds > 1 else ''}"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''}"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if minutes > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} and {minutes} minute{'s' if minutes > 1 else ''}"
        return f"{hours} hour{'s' if hours > 1 else ''}"


def format_time_for_tts(dt: datetime) -> str:
    """
    Converts a datetime object into a human-friendly time string for TTS.
    """
    return dt.strftime("%I:%M %p").lstrip("0")  # E.g., "2:30 PM"


@dataclasses.dataclass
class Timer:
    alarm_time: datetime = None
    description: str = None


def _timer_nlp_extract(text: str) -> dict:
    """Extract timer parameters from natural language."""
    params = {}
    duration = parse_duration(text)
    if duration:
        params["hours"] = duration["hours"]
        params["minutes"] = duration["minutes"]
        params["seconds"] = duration["seconds"]
    desc = extract_after_keyword(text, ["called", "named", "for"])
    # Only use as description if it doesn't look like a duration
    if desc and not parse_duration(desc):
        params["description"] = desc
    return params


def _timer_nlp_response(result: dict) -> str:
    if result.get("status") == "error":
        return result.get("message", "Error setting timer.")
    msg = result.get("message", "Timer set.")
    expires = result.get("expires_at", "")
    if expires:
        return f"{msg} It will go off at {expires}."
    return msg


def _list_timers_nlp_response(result: dict) -> str:
    if result.get("status") == "empty":
        return "No active timers."
    timers = result.get("timers", [])
    if not timers:
        return "No active timers."
    parts = []
    for t in timers:
        parts.append(f"{t.get('description', 'Timer')}, {t.get('expires_in', '')} remaining")
    return "Active timers: " + ". ".join(parts) + "."


def _cancel_timer_nlp_extract(text: str) -> dict:
    """Extract timer query for cancellation."""
    import re
    cleaned = re.sub(
        r"^(?:please\s+)?(?:cancel|delete|remove|stop|kill)\s+(?:the\s+)?(?:timer\s+)?(?:called\s+|named\s+|for\s+)?",
        "", text.strip(), flags=re.IGNORECASE
    ).strip()
    # If the cleaned text looks like a duration, convert to description format
    dur = parse_duration(cleaned)
    if dur:
        parts = []
        if dur["hours"]:
            parts.append(f"{dur['hours']} hour")
        if dur["minutes"]:
            parts.append(f"{dur['minutes']} minute")
        if dur["seconds"]:
            parts.append(f"{dur['seconds']} second")
        cleaned = "the " + " ".join(parts) + " timer"
    return {"query": cleaned} if cleaned else {"query": text.strip()}


def _cancel_timer_nlp_response(result: dict) -> str:
    if result.get("status") == "success":
        msg = result.get("message", "Timer cancelled.")
        # Make it more natural: "Cancelled timer: the 5 minutes timer" → "Done, cancelled the 5 minutes timer."
        return msg.replace("Cancelled timer: ", "Done, cancelled ") + "."
    if result.get("status") == "info":
        return result.get("message", "No active timers.")
    return result.get("message", "No matching timer found.")


class CountdownTimer(RunnableMCPPlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            logger.info("Instantiating singleton")
            cls._instance = super(CountdownTimer, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()
        self._data_dir = os.environ.get("TIMER_DATA_DIR") or os.path.join("plugin_data", "timers")
        os.makedirs(self._data_dir, exist_ok=True)
        self.timers: List[Timer] = []
        # Unified ringing state — manages both timers and alarms
        import threading as _thr
        self._ringing_items = []  # list of {"description": str, "type": "timer"|"alarm"}
        self._ring_stop = _thr.Event()
        self._ring_thread = None
        self._ring_active = False
        logger.info("CountdownTimer system initializing.")

        # Load persisted timers (removes expired ones)
        self._load_timers()

        self.register_tool(
            handler=self.set_timer,
            description="Set a timer for a specified duration and an optional description.",
            parameters={
                "hours": {"type": "integer", "description": "Number of hours for the timer."},
                "minutes": {"type": "integer", "description": "Number of minutes for the timer."},
                "seconds": {"type": "integer", "description": "Number of seconds for the timer."},
                "description": {"type": "string", "description": "Optional description for the timer."},
            },
            intents=[
                "set a timer for twelve minutes",
                "start a countdown timer for 45 seconds",
                "set a timer for 1 hour and 2 minutes",
                "timer for 60 seconds",
                "set a timer for 5 minutes",
                "countdown 10 minutes",
                "timer 30 seconds",
                "set a 15 minute timer",
                "start a timer for 20 minutes",
                "set a timer for the eggs",
                "timer for 3 minutes please",
                "set a cooking timer for 10 minutes",
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.COOKING],
            nlp_extract_fn=_timer_nlp_extract,
            nlp_response=_timer_nlp_response,
        )

        self.register_tool(
            handler=self.list_timers,
            description="List all active timers with their descriptions and expiration times.",
            intents=[
                "list all timers",
                "what timers are active",
                "how much time left on my egg timer",
                "show my timers",
                "any timers running",
                "check my timers",
                "how long left on the timer",
                "are there any timers set",
                "what timers do I have",
                "timer status",
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.COOKING],
            nlp_response=_list_timers_nlp_response,
        )

        self.register_tool(
            handler=self.cancel_timer,
            description="Cancel and remove an active timer. Call this directly when the user wants to cancel, stop, or remove a timer.",
            parameters={
                "query": {
                    "type": "string",
                    "description": "The timer description or duration to cancel, e.g. 'egg timer' or '5 minute timer'.",
                },
            },
            required=["query"],
            intents=[
                "cancel the timer",
                "cancel my timer",
                "cancel the egg timer",
                "cancel the pasta timer",
                "cancel the cooking timer",
                "cancel the countdown timer",
                "remove the timer",
                "remove the egg timer",
                "delete the timer",
                "cancel timer",
                "turn off the timer",
                "remove that timer",
                "cancel countdown timer",
            ],
            process_output=False,
            activity=[Activity.UTILITIES, Activity.COOKING],
            nlp_extract_fn=_cancel_timer_nlp_extract,
            nlp_response=_cancel_timer_nlp_response,
        )

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def _save_timers(self):
        """Persist active timers to JSON."""
        data = [
            {"alarm_time": t.alarm_time.isoformat(), "description": t.description}
            for t in self.timers
        ]
        path = os.path.join(self._data_dir, "timers.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save timers: {e}")

    def _load_timers(self):
        """Load persisted timers, discarding expired ones."""
        path = os.path.join(self._data_dir, "timers.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = datetime.now()
            loaded = 0
            for entry in data:
                alarm_time = datetime.fromisoformat(entry["alarm_time"])
                if alarm_time > now:
                    self.timers.append(Timer(alarm_time=alarm_time, description=entry["description"]))
                    loaded += 1
            if loaded:
                logger.info(f"Restored {loaded} timer(s) from disk")
            # Clean up expired entries from file
            self._save_timers()
        except Exception as e:
            logger.warning(f"Failed to load timers: {e}")

    def add_timer(self, alarm_time: datetime, description: str):
        self.timers.append(Timer(alarm_time=alarm_time, description=description))
        logger.info(f"Added timer: {description}, expires at {format_time_for_tts(alarm_time)}.")
        self._save_timers()
        # Push timer view to display immediately so user sees the countdown
        self._publish_timer_display()

    def remove_expired_timers(self):
        now = datetime.now()
        expired_timers = [timer for timer in self.timers if timer.alarm_time <= now]
        self.timers = [timer for timer in self.timers if timer.alarm_time > now]
        if expired_timers:
            self._save_timers()
        return expired_timers

    def set_timer(self,
                  hours: Optional[int] = 0,
                  minutes: Optional[int] = 0,
                  seconds: Optional[int] = 0,
                  description: Optional[str] = None):
        try:
            total_seconds = timedelta(
                hours=int(hours or 0),
                minutes=int(minutes or 0),
                seconds=int(seconds or 0)
            ).total_seconds()
            if total_seconds <= 0:
                return {"status": "error", "message": "Duration must be greater than 0 seconds."}

            alarm_time = datetime.now() + timedelta(seconds=total_seconds)
            duration_str = format_duration(int(total_seconds))
            description = description or f"the {duration_str} timer"
            self.add_timer(alarm_time, description)

            return {
                "status": "done",
                "message": f"Timer called: '{description}'",
                "expires_at": format_time_for_tts(alarm_time),
            }
        except Exception as e:
            logger.error(f"Error setting timer: {e}")
            return {"status": "error", "message": str(e)}

    def list_timers(self):
        if not self.timers:
            return {"status": "empty", "message": "No active timers."}

        now = datetime.now()
        timers_info = [
            {
                "description": timer.description,
                "expires_in": format_duration(int((timer.alarm_time - now).total_seconds())),
                "expires_at": format_time_for_tts(timer.alarm_time),
            }
            for timer in self.timers
        ]
        return {"status": "success", "timers": timers_info}

    def cancel_timer(self, query: str) -> dict:
        """Cancel a timer by description or duration match."""
        if not self.timers:
            return {"status": "info", "message": "No active timers."}

        query_lower = query.strip().lower()

        # Try matching by description (fuzzy substring)
        for i, timer in enumerate(self.timers):
            if query_lower in timer.description.lower():
                removed = self.timers.pop(i)
                logger.info(f"Cancelled timer: {removed.description}")
                self._save_timers()
                self._publish_timer_display()
                return {"status": "success", "message": f"Cancelled timer: {removed.description}"}

        # If only one timer, cancel it regardless of query
        if len(self.timers) == 1:
            removed = self.timers.pop(0)
            logger.info(f"Cancelled only active timer: {removed.description}")
            self._save_timers()
            self._publish_timer_display()
            return {"status": "success", "message": f"Cancelled timer: {removed.description}"}

        return {"status": "error", "message": f"No timer found matching '{query}'. Say 'list timers' to see active timers."}

    def _publish_timer_display(self):
        """Publish current timers + alarms to the display."""
        now = datetime.now()

        # Gather active timers
        timers_info = [
            {
                "description": timer.description,
                "expires_in": format_duration(int((timer.alarm_time - now).total_seconds())),
                "expires_at": format_time_for_tts(timer.alarm_time),
                "type": "timer",
            }
            for timer in self.timers
        ]

        # Include ringing items (expired timers and alarms, not yet dismissed)
        for item in self._ringing_items:
            timers_info.append({
                "description": item["description"],
                "expires_in": "DONE",
                "expires_at": "",
                "type": item["type"] + "_done",  # "timer_done" or "alarm_done"
            })

        # Gather active alarms from existing plugin instance (don't instantiate a new one)
        try:
            from glados.system.plugin import PluginSystem
            alarm_entry = PluginSystem().plugins.get("alarmclock", {})
            alarm_plugin = alarm_entry.get("function") if alarm_entry else None
            if alarm_plugin and hasattr(alarm_plugin, "alarms"):
                for alarm in list(alarm_plugin.alarms):
                    remaining = int((alarm.alarm_time - now).total_seconds())
                    timers_info.append({
                        "description": alarm.description,
                        "expires_in": format_duration(remaining) if remaining > 0 else "now",
                        "expires_at": alarm.alarm_time.strftime("%I:%M %p").lstrip("0"),
                        "type": "alarm",
                    })
        except Exception as e:
            logger.debug(f"Could not fetch alarms for display: {e}")

        if not timers_info:
            # No active timers or alarms — clear the timer overlay
            # (send an empty timer update rather than idle, so existing
            # content like recipes isn't disrupted)
            self.event_system.publish(
                EventMessage(
                    role="display",
                    name="timer",
                    content={"title": "", "timers": []},
                    process_output=False
                )
            )
            return

        self.event_system.publish(
            EventMessage(
                role="display",
                name="timer",
                content={
                    "title": "Timers & Alarms",
                    "timers": timers_info,
                },
                process_output=False
            )
        )

    def _check_timers(self, event: EventMessage):
        logger.debug("Checking timers")

        # Publish live countdown to display while timers are active
        if self.timers:
            self._publish_timer_display()

        expired_timers = self.remove_expired_timers()

        for timer in expired_timers:
            logger.info(f"Timer expired: '{timer.description}', firing event")

            # Start unified ringing
            self._start_ringing(timer.description, "timer")

            self.event_system.publish(
                EventMessage(
                    role="tool",
                    name="timer_expired",
                    content={"message": f"The timer '{timer.description}' has finished. Announce this to the user briefly. Do NOT set a new timer."},
                    process_output=True
                )
            )

        # After processing expirations, update display (clears to idle if nothing left)
        if expired_timers:
            self._publish_timer_display()

    # -----------------------------------------------------------------------
    # Timer ringing (persistent alert until dismissed or timeout)
    # -----------------------------------------------------------------------

    _RING_TIMEOUT = 180  # 3 minutes max ringing

    def _on_start_ring(self, event: EventMessage):
        """Handle ring events from both timer expiry and alarm expiry."""
        data = event.content if isinstance(event.content, dict) else {}
        description = data.get("description", "Alert")
        ring_type = data.get("type", "timer")
        self._start_ringing(description, ring_type)

    def _start_ringing(self, description: str, ring_type: str = "timer"):
        """Start or add to the unified ring. Supports multiple concurrent items."""
        import os

        self._ringing_items.append({"description": description, "type": ring_type})
        self._publish_timer_display()

        # If ring thread is already running, just add to the list — it will keep playing
        if self._ring_active:
            logger.info(f"Added '{description}' ({ring_type}) to ringing items")
            return

        self._ring_active = True
        self._ring_stop.clear()

        alert_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sounds", "timer_alert.wav")
        if not os.path.exists(alert_path):
            logger.warning(f"Alert sound not found: {alert_path}")
            return

        def ring_loop():
            import subprocess
            import time as _time
            start = _time.monotonic()
            while not self._ring_stop.is_set() and self._ringing_items:
                if _time.monotonic() - start > self._RING_TIMEOUT:
                    logger.info(f"Ring timeout after {self._RING_TIMEOUT}s")
                    break
                try:
                    proc = subprocess.Popen(["afplay", alert_path])
                    proc.wait(timeout=5)
                except Exception as e:
                    logger.debug(f"Ring playback error: {e}")
                self._ring_stop.wait(timeout=2.0)
            self._ring_active = False
            logger.info("Ring loop stopped.")
            # Don't clear ringing_items here — dismiss_ring clears them
            # (timeout auto-clears)
            if not self._ringing_items:
                return
            if _time.monotonic() - start >= self._RING_TIMEOUT:
                self._ringing_items.clear()
                self._publish_timer_display()

        import threading
        self._ring_thread = threading.Thread(target=ring_loop, daemon=True)
        self._ring_thread.start()
        logger.info(f"Ringing started: {description} ({ring_type})")

    def dismiss_ring(self) -> bool:
        """Stop ALL ringing (timers and alarms) and clear from display."""
        if not self._ring_active and not self._ringing_items:
            return False
        self._ring_stop.set()
        items = list(self._ringing_items)
        self._ringing_items.clear()
        self._ring_active = False

        # Also dismiss any ringing alarm in the AlarmClock plugin
        try:
            from glados.system.plugin import PluginSystem
            alarm_entry = PluginSystem().plugins.get("alarmclock", {})
            alarm_plugin = alarm_entry.get("function") if alarm_entry else None
            if alarm_plugin and hasattr(alarm_plugin, "dismiss"):
                alarm_plugin.dismiss()
        except Exception as e:
            logger.debug(f"Could not dismiss alarm: {e}")

        for item in items:
            logger.info(f"Dismissed: {item['description']} ({item['type']})")
        self._publish_timer_display()
        return True

    def _on_timer_action(self, event):
        """Handle direct timer/alarm UI actions (no LLM round-trip).

        UI clicks don't speak confirmation — the user can see the result on screen.
        """
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")

        if action == "create":
            minutes = int(data.get("minutes", 0))
            if minutes > 0:
                self.set_timer(minutes=minutes)

        elif action == "cancel":
            description = data.get("description", "")
            item_type = data.get("type", "")

            # Try alarm cancel first if type says alarm, OR if no timer matches
            cancelled = False

            if item_type != "alarm" and description:
                # Try timer cancel — exact match only (no single-timer fallback from UI)
                for i, timer in enumerate(self.timers):
                    if description.lower() in timer.description.lower():
                        self.timers.pop(i)
                        self._save_timers()
                        logger.info(f"UI cancelled timer: {timer.description}")
                        cancelled = True
                        break

            if not cancelled:
                # Try alarm cancel
                try:
                    alarm_plugin = self._get_alarm_plugin()
                    if alarm_plugin:
                        result = alarm_plugin.cancel_alarm(description)
                        if result.get("status") == "success":
                            cancelled = True
                            logger.info(f"UI cancelled alarm: {description}")
                except Exception as e:
                    logger.warning(f"Failed to cancel alarm: {e}")

            self._publish_timer_display()

        elif action == "dismiss_ring":
            self.dismiss_ring()

    def _get_alarm_plugin(self):
        """Get the existing AlarmClock plugin instance from the plugin registry."""
        try:
            from glados.system.plugin import PluginSystem
            for name, pd in PluginSystem().plugins.items():
                if "alarm" in name.lower() and hasattr(pd.get("function"), "cancel_alarm"):
                    return pd["function"]
        except Exception:
            pass
        return None

    def start(self):
        logger.info("Starting CountdownTimer.")
        self.event_system.subscribe("system.tick", EventHook("check_timers", callback=self._check_timers, priority=5))

        # Register UI action for direct timer control from display
        self.register_ui_action("timer_action", self._on_timer_action)
        self.event_system.subscribe(
            "ui.timer_action",
            EventHook("timer_ui_handler", callback=self._on_timer_action, priority=5)
        )

        # Unified ring event — both timers and alarms route here
        self.event_system.subscribe(
            "system.start_ring",
            EventHook("start_ring", callback=self._on_start_ring, priority=5)
        )

        # Listen for interrupt events to dismiss all ringing
        self.event_system.subscribe(
            "system.interrupt_tts",
            EventHook("ring_dismiss_on_interrupt", callback=lambda e: self.dismiss_ring(), priority=1)
        )

        # Publish display on startup so restored timers/alarms are visible
        if self.timers:
            logger.info(f"Restored {len(self.timers)} timer(s) — publishing to display")
            self._publish_timer_display()

    def stop(self):
        logger.info("Stopping CountdownTimer.")
