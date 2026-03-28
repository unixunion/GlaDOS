import dataclasses
import os
import re
import threading
import wave
from datetime import datetime
from typing import List, Optional

import numpy as np
import sounddevice as sd
import dateparser
from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage, EventHook


def _alarm_nlp_extract(text: str) -> dict:
    """Extract alarm time from natural language."""
    # Remove the intent prefix like "set an alarm for"
    time_str = re.sub(
        r"^(?:please\s+)?(?:set\s+(?:an?\s+)?alarm\s+(?:for|at)\s+)",
        "", text, flags=re.IGNORECASE
    ).strip()
    if not time_str:
        time_str = text
    return {"time": time_str}


def _alarm_nlp_response(result: dict) -> str:
    if result.get("status") == "error":
        return result.get("message", "Error setting alarm.")
    return result.get("message", "Alarm set.")


def _get_alarms_nlp_response(result: dict) -> str:
    if not result.get("alarms"):
        return result.get("message", "No alarms set.")
    alarms = result["alarms"]
    parts = [f"{a['description']} at {a['time']}" for a in alarms]
    return "Current alarms: " + ". ".join(parts) + "."


def _cancel_alarm_nlp_extract(text: str) -> dict:
    """Extract alarm query for cancellation."""
    query = re.sub(
        r"^(?:please\s+)?(?:cancel|delete|remove)\s+(?:the\s+)?(?:alarm\s+)?(?:for\s+|at\s+)?",
        "", text, flags=re.IGNORECASE
    ).strip()
    return {"query": query or text}


@dataclasses.dataclass
class Alarm:
    alarm_time: datetime
    description: str


class AlarmClock(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        logger.info("Instantiating Alarm Clock System")
        self.alarms: List[Alarm] = []
        self._lock = threading.Lock()

        # Ringing state
        self.ringing = False
        self._ringing_alarm: Optional[Alarm] = None
        self._ring_thread: Optional[threading.Thread] = None
        self._ring_stop = threading.Event()

        # Load alert sound
        self._alert_audio = None
        self._alert_rate = 16000
        alert_path = os.path.join(os.getcwd(), "sounds", "timer_alert.wav")
        if os.path.exists(alert_path):
            try:
                with wave.open(alert_path, "rb") as wf:
                    raw = wf.readframes(wf.getnframes())
                    self._alert_audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
                    self._alert_rate = wf.getframerate()
                    channels = wf.getnchannels()
                    if channels > 1:
                        self._alert_audio = self._alert_audio.reshape(-1, channels)
                    else:
                        self._alert_audio = self._alert_audio.reshape(-1, 1)
                logger.info(f"Loaded alarm alert sound: {alert_path}")
            except Exception as e:
                logger.warning(f"Could not load alarm sound: {e}")

        # Register tools
        self.register_tool(
            handler=self.set_fixed_time_alarm,
            description="Sets an alarm at a fixed time (natural language format).",
            parameters={
                "time": {
                    "type": "string",
                    "description": "The time for the alarm in natural language, e.g., '5pm tomorrow'."
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for the alarm."
                },
            },
            required=["time"],
            intents=[
                "set an alarm for five o clock",
                "set an alarm for 5pm tomorrow",
                "set the alarm for 8:30 am on Sunday",
                "set an alarm for next Monday at noon",
                "wake me up at 7am",
                "alarm for 6:30 in the morning",
                "set an alarm for 10pm",
                "alarm at 9 o clock",
                "set a morning alarm for 7:30",
                "remind me at 3pm",
                "set alarm for midnight",
                "alarm for tomorrow at 8am",
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_alarm_nlp_extract,
            nlp_response=_alarm_nlp_response,
        )

        self.register_tool(
            handler=self.get_alarms,
            description="List all currently set alarms. Only use this to CHECK or LIST alarms — to cancel or remove an alarm, use cancel_alarm instead.",
            intents=[
                "get all alarms",
                "list my alarms",
                "what alarms are set",
                "show my alarms",
                "do I have any alarms",
                "check my alarms",
                "any alarms set",
                "what alarms do I have",
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING],
            nlp_response=_get_alarms_nlp_response,
        )

        self.register_tool(
            handler=self.cancel_alarm,
            description="Cancel, delete, or turn off an alarm. Do NOT use get_alarms first — call this directly when the user wants to remove, dismiss, silence, or cancel an alarm.",
            parameters={
                "query": {
                    "type": "string",
                    "description": "The alarm description or time to cancel, e.g., 'morning alarm' or '5pm'."
                },
            },
            required=["query"],
            intents=[
                "cancel the alarm",
                "delete the 5pm alarm",
                "remove alarm",
                "cancel my morning alarm",
                "cancel alarm",
                "turn off the alarm",
                "stop the alarm",
                "clear the alarm",
                "delete alarm",
                "remove the alarm for 8am",
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_cancel_alarm_nlp_extract,
        )

    def start(self):
        logger.info("Starting Alarm Clock System...")
        self.event_system.subscribe(
            "system.tick",
            EventHook("alarm_check", callback=self._check_alarms, priority=5)
        )
        logger.success("Alarm Clock System started")

    def stop(self):
        logger.info("Stopping Alarm Clock System...")
        self.dismiss()
        self.event_system.unsubscribe("system.tick", "alarm_check")

    @staticmethod
    def _format_time_for_speech(dt: datetime) -> str:
        return dt.strftime("%I:%M %p on %A").lstrip("0")

    def set_fixed_time_alarm(self, time: str, description: Optional[str] = None):
        try:
            if not time:
                return {"status": "error", "message": "The 'time' field is required for setting an alarm."}

            alarm_time = dateparser.parse(time, settings={"PREFER_DATES_FROM": "future"})
            if not alarm_time:
                return {"status": "error", "message": f"Could not parse the time: '{time}'."}

            if alarm_time < datetime.now():
                return {"status": "error", "message": "Cannot set an alarm for a past time."}

            if not description:
                description = f"Alarm at {self._format_time_for_speech(alarm_time)}"

            with self._lock:
                for alarm in self.alarms:
                    if alarm.alarm_time == alarm_time:
                        return {"status": "error", "message": "An alarm is already set for this time."}

                self.alarms.append(Alarm(alarm_time=alarm_time, description=description))
                # Push timer/alarm display so the new alarm appears immediately
                try:
                    from plugins.basic.countdown_timer import CountdownTimer
                    CountdownTimer()._publish_timer_display()
                except Exception:
                    pass
                return {
                    "status": "success",
                    "message": f"Alarm set for {self._format_time_for_speech(alarm_time)}.",
                    "description": description,
                }
        except Exception as e:
            logger.exception(f"Error setting fixed-time alarm: {str(e)}")
            return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}

    def get_alarms(self):
        with self._lock:
            if not self.alarms:
                return {"status": "success", "message": "No alarms are currently set.", "alarms": []}
            alarms_list = [
                {"time": self._format_time_for_speech(alarm.alarm_time), "description": alarm.description}
                for alarm in self.alarms
            ]
            return {"status": "success", "message": "Currently set alarms:", "alarms": alarms_list}

    def _refresh_timer_display(self):
        """Trigger the timer display to refresh (shows timers + alarms, or clears to idle)."""
        try:
            from plugins.basic.countdown_timer import CountdownTimer
            CountdownTimer()._publish_timer_display()
        except Exception:
            pass

    def cancel_alarm(self, query: str) -> dict:
        query_lower = query.strip().lower()
        with self._lock:
            if not self.alarms:
                return {"status": "info", "message": "No alarms are currently set."}

            for i, alarm in enumerate(self.alarms):
                if query_lower in alarm.description.lower():
                    removed = self.alarms.pop(i)
                    self._refresh_timer_display()
                    return {"status": "success", "message": f"Cancelled alarm: {removed.description}"}

            parsed_time = dateparser.parse(query)
            if parsed_time:
                for i, alarm in enumerate(self.alarms):
                    if alarm.alarm_time.hour == parsed_time.hour and alarm.alarm_time.minute == parsed_time.minute:
                        removed = self.alarms.pop(i)
                        self._refresh_timer_display()
                        return {"status": "success", "message": f"Cancelled alarm: {removed.description}"}

            return {"status": "error", "message": f"No alarm found matching '{query}'."}

    def _ring_loop(self):
        logger.info("Alarm ring loop started.")
        while not self._ring_stop.is_set():
            if self._alert_audio is not None:
                try:
                    stream = sd.OutputStream(
                        samplerate=self._alert_rate,
                        channels=self._alert_audio.shape[1] if self._alert_audio.ndim > 1 else 1,
                        dtype="float32",
                    )
                    stream.start()
                    stream.write(self._alert_audio)
                    stream.stop()
                    stream.close()
                except Exception as e:
                    logger.debug(f"Ring tone playback error: {e}")
            self._ring_stop.wait(timeout=2.0)
        logger.info("Alarm ring loop stopped.")

    def _start_ringing(self, alarm: Alarm):
        if self.ringing:
            return
        self.ringing = True
        self._ringing_alarm = alarm
        self._ring_stop.clear()

        self.event_system.publish(EventMessage("system", "music_pause", {}))
        self.event_system.publish(EventMessage(
            role="display", name="timer",
            content={"title": f"{alarm.description}", "content": "ALARM", "alert": True},
            process_output=False
        ))

        self._ring_thread = threading.Thread(target=self._ring_loop, daemon=True)
        self._ring_thread.start()
        logger.info(f"Alarm ringing: {alarm.description}")

    def dismiss(self) -> bool:
        if not self.ringing:
            return False
        self._ring_stop.set()
        if self._ring_thread and self._ring_thread.is_alive():
            self._ring_thread.join(timeout=3)
        self._ring_thread = None
        dismissed = self._ringing_alarm
        self.ringing = False
        self._ringing_alarm = None
        self.event_system.publish(EventMessage("system", "music_resume", {}))
        if dismissed:
            logger.info(f"Alarm dismissed: {dismissed.description}")
        self._refresh_timer_display()
        return True

    def _check_alarms(self, event: EventMessage):
        now = datetime.now()
        with self._lock:
            expired = [a for a in self.alarms if a.alarm_time <= now]
            self.alarms = [a for a in self.alarms if a.alarm_time > now]

        for alarm in expired:
            logger.info(f"Alarm expired: {alarm.description}")
            self._start_ringing(alarm)
            self.event_system.publish(EventMessage(
                "tool", "alarm_triggered",
                f"Alarm '{alarm.description}' is ringing. Tell the user their alarm is going off. Do NOT set a new alarm.",
                process_output=True
            ))
            break

        if expired:
            self._refresh_timer_display()
