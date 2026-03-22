import dataclasses
from datetime import datetime, timedelta
from typing import Optional, List
from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
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
                "Set a timer for twelve minutes",
                "Start a countdown timer for 45 seconds",
                "Set a timer for 1 hour and 2 minutes",
                "Timer for 60 seconds"
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.COOKING],
        )

        self.register_tool(
            handler=self.list_timers,
            description="List all active timers with their descriptions and expiration times.",
            intents=[
                "list all timers",
                "what timers are active",
                "how much time left on my egg timer"
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.COOKING],
        )

    def add_timer(self, alarm_time: datetime, description: str):
        self.timers.append(Timer(alarm_time=alarm_time, description=description))
        logger.info(f"Added timer: {description}, expires at {format_time_for_tts(alarm_time)}.")

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

    def _check_timers(self, event: EventMessage):
        logger.debug("Checking timers")
        expired_timers = self.remove_expired_timers()

        for timer in expired_timers:
            logger.info(f"Timer called: '{timer.description}' has expired, firing event")

            self.event_system.publish(
                EventMessage(
                    role="tool",
                    name="set_timer",
                    content={"message": f"A timer called: '{timer.description}' has expired."},
                    process_output=True
                )
            )

            self.event_system.publish(
                EventMessage(
                    role="display",
                    name="timer",
                    content={
                        "title": f"{timer.description} - Time's Up!",
                        "content": "DONE",
                        "alert": True,
                    },
                    process_output=False
                )
            )

            try:
                import subprocess
                import os
                alert_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sounds", "timer_alert.wav")
                if os.path.exists(alert_path):
                    subprocess.Popen(["afplay", alert_path])
            except Exception as e:
                logger.debug(f"Could not play timer alert sound: {e}")

    def start(self):
        logger.info("Starting CountdownTimer.")
        self.event_system.subscribe("system.tick", EventHook("check_timers", callback=self._check_timers, priority=5))

    def stop(self):
        logger.info("Stopping CountdownTimer.")
