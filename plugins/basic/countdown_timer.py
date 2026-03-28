import dataclasses
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
        self.timers: List[Timer] = []
        logger.info("CountdownTimer system initializing.")

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

    def add_timer(self, alarm_time: datetime, description: str):
        self.timers.append(Timer(alarm_time=alarm_time, description=description))
        logger.info(f"Added timer: {description}, expires at {format_time_for_tts(alarm_time)}.")
        # Push timer view to display immediately so user sees the countdown
        self._publish_timer_display()

    def remove_expired_timers(self):
        now = datetime.now()
        expired_timers = [timer for timer in self.timers if timer.alarm_time <= now]
        self.timers = [timer for timer in self.timers if timer.alarm_time > now]
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
                self._publish_timer_display()
                return {"status": "success", "message": f"Cancelled timer: {removed.description}"}

        # If only one timer, cancel it regardless of query
        if len(self.timers) == 1:
            removed = self.timers.pop(0)
            logger.info(f"Cancelled only active timer: {removed.description}")
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

            # Start persistent ringing (like alarm system)
            self._start_timer_ring(timer)

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

    def _start_timer_ring(self, timer):
        """Start persistent ringing for an expired timer."""
        import os
        import threading

        self._ring_stop = getattr(self, '_ring_stop', threading.Event())
        if getattr(self, '_ring_active', False):
            return  # Already ringing

        self._ring_active = True
        self._ringing_timer = timer
        self._ring_stop.clear()

        # Flash display
        self.event_system.publish(EventMessage(
            role="display", name="timer",
            content={"title": f"{timer.description} - Time's Up!", "content": "DONE", "alert": True},
            process_output=False
        ))

        # Load alert sound
        alert_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sounds", "timer_alert.wav")
        if not os.path.exists(alert_path):
            logger.warning(f"Timer alert sound not found: {alert_path}")
            self._ring_active = False
            return

        def ring_loop():
            import subprocess
            import time
            start = time.monotonic()
            while not self._ring_stop.is_set():
                if time.monotonic() - start > self._RING_TIMEOUT:
                    logger.info(f"Timer ring timeout after {self._RING_TIMEOUT}s")
                    break
                try:
                    proc = subprocess.Popen(["afplay", alert_path])
                    proc.wait(timeout=5)
                except Exception as e:
                    logger.debug(f"Ring playback error: {e}")
                self._ring_stop.wait(timeout=2.0)
            self._ring_active = False
            self._ringing_timer = None
            logger.info("Timer ring loop stopped.")
            self._publish_timer_display()

        self._ring_thread = threading.Thread(target=ring_loop, daemon=True)
        self._ring_thread.start()
        logger.info(f"Timer ringing: {timer.description}")

    def dismiss_timer_ring(self) -> bool:
        """Stop the timer ring. Called by voice commands or UI."""
        if not getattr(self, '_ring_active', False):
            return False
        self._ring_stop.set()
        dismissed = getattr(self, '_ringing_timer', None)
        if dismissed:
            logger.info(f"Timer ring dismissed: {dismissed.description}")
        return True

    def _on_timer_action(self, event):
        """Handle direct timer UI actions (no LLM round-trip)."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")

        if action == "create":
            minutes = int(data.get("minutes", 0))
            if minutes > 0:
                result = self.set_timer(minutes=minutes)
                self.event_system.publish(EventMessage(
                    "tts", "speak", result.get("message", "Timer set.")
                ))

        elif action == "cancel":
            description = data.get("description", "")
            if description:
                result = self.cancel_timer(description)
            elif len(self.timers) == 1:
                result = self.cancel_timer("")
            else:
                result = {"message": "No timer specified."}
            self.event_system.publish(EventMessage(
                "tts", "speak", result.get("message", "Done.")
            ))

        elif action == "dismiss_ring":
            self.dismiss_timer_ring()

    def start(self):
        logger.info("Starting CountdownTimer.")
        self.event_system.subscribe("system.tick", EventHook("check_timers", callback=self._check_timers, priority=5))

        # Register UI action for direct timer control from display
        self.register_ui_action("timer_action", self._on_timer_action)
        self.event_system.subscribe(
            "ui.timer_action",
            EventHook("timer_ui_handler", callback=self._on_timer_action, priority=5)
        )

        # Listen for interrupt events to dismiss ringing timers
        self.event_system.subscribe(
            "system.interrupt_tts",
            EventHook("timer_dismiss_on_interrupt", callback=lambda e: self.dismiss_timer_ring(), priority=1)
        )

    def stop(self):
        logger.info("Stopping CountdownTimer.")
