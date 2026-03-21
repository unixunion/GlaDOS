import dataclasses
import os
import threading
import wave
from datetime import datetime
from typing import List, Optional

import numpy as np
import sounddevice as sd
import dateparser
from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.event_system import EventSystem, EventMessage, EventHook
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()


@dataclasses.dataclass
class Alarm:
    alarm_time: datetime
    description: str


class AlarmClock(RunnablePlugin):

    def __init__(self):
        super().__init__()
        logger.info("Instantiating Alarm Clock System")
        self.alarms: List[Alarm] = []
        self.event_system = EventSystem()
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
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Sets an alarm at a fixed time (natural language format).",
                    parameters=Parameters(
                        type="object",
                        properties={
                            "time": ParameterType(
                                type="string",
                                description="The time for the alarm in natural language, e.g., '5pm tomorrow'."
                            ),
                            "description": ParameterType(
                                type="string",
                                description="Optional description for the alarm."
                            )
                        },
                        required=["time"],
                        additionalProperties=False
                    )
                )),
            intents=[
                "set an alarm for five o clock",
                "set an alarm for 5pm tomorrow",
                "set the alarm for 8:30 am on Sunday",
                "set an alarm for next Monday at noon"
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING]
        )(self.set_fixed_time_alarm)

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Retrieves all currently set alarms.",
                    parameters=Parameters(
                        type="object",
                        properties={},
                        required=[],
                        additionalProperties=False
                    )
                )
            ),
            intents=["get all alarms", "list my alarms", "what alarms are set?"],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING]
        )(self.get_alarms)

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Cancels an alarm by its description or time.",
                    parameters=Parameters(
                        type="object",
                        properties={
                            "query": ParameterType(
                                type="string",
                                description="The alarm description or time to cancel, e.g., 'morning alarm' or '5pm'."
                            )
                        },
                        required=["query"],
                        additionalProperties=False
                    )
                )
            ),
            intents=[
                "cancel the alarm",
                "delete the 5pm alarm",
                "remove alarm",
                "cancel my morning alarm"
            ],
            process_output=True,
            activity=[Activity.UTILITIES, Activity.GENERAL, Activity.COOKING]
        )(self.cancel_alarm)

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
        """Format a datetime for natural speech output."""
        return dt.strftime("%I:%M %p on %A").lstrip("0")

    def set_fixed_time_alarm(self, time: str, description: Optional[str] = None):
        """Register a new fixed-time alarm."""
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
                return {
                    "status": "success",
                    "message": f"Alarm set for {self._format_time_for_speech(alarm_time)}.",
                    "description": description,
                }
        except Exception as e:
            logger.exception(f"Error setting fixed-time alarm: {str(e)}")
            return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}

    def get_alarms(self):
        """Retrieve all currently set alarms."""
        with self._lock:
            if not self.alarms:
                return {"status": "success", "message": "No alarms are currently set.", "alarms": []}

            alarms_list = [
                {
                    "time": self._format_time_for_speech(alarm.alarm_time),
                    "description": alarm.description,
                }
                for alarm in self.alarms
            ]
            return {"status": "success", "message": "Currently set alarms:", "alarms": alarms_list}

    def cancel_alarm(self, query: str) -> dict:
        """Cancel an alarm matching the query by description or time."""
        query_lower = query.strip().lower()
        with self._lock:
            if not self.alarms:
                return {"status": "info", "message": "No alarms are currently set."}

            for i, alarm in enumerate(self.alarms):
                if query_lower in alarm.description.lower():
                    removed = self.alarms.pop(i)
                    return {"status": "success", "message": f"Cancelled alarm: {removed.description}"}

            parsed_time = dateparser.parse(query)
            if parsed_time:
                for i, alarm in enumerate(self.alarms):
                    if alarm.alarm_time.hour == parsed_time.hour and alarm.alarm_time.minute == parsed_time.minute:
                        removed = self.alarms.pop(i)
                        return {"status": "success", "message": f"Cancelled alarm: {removed.description}"}

            return {"status": "error", "message": f"No alarm found matching '{query}'."}

    # --- Ringing ---

    def _ring_loop(self):
        """Loop the alarm tone with a gap between cycles until dismissed."""
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
            # Wait 2 seconds between cycles, but check for stop frequently
            self._ring_stop.wait(timeout=2.0)
        logger.info("Alarm ring loop stopped.")

    def _start_ringing(self, alarm: Alarm):
        """Start the alarm ringing loop and pause music."""
        if self.ringing:
            return  # Already ringing

        self.ringing = True
        self._ringing_alarm = alarm
        self._ring_stop.clear()

        # Pause music if playing
        self.event_system.publish(EventMessage("system", "music_pause", {}))

        # Flash the display
        self.event_system.publish(EventMessage(
            role="display",
            name="timer",
            content={
                "title": f"{alarm.description}",
                "content": "ALARM",
                "alert": True,
            },
            process_output=False
        ))

        self._ring_thread = threading.Thread(target=self._ring_loop, daemon=True)
        self._ring_thread.start()
        logger.info(f"Alarm ringing: {alarm.description}")

    def dismiss(self) -> bool:
        """Stop the ringing alarm. Returns True if an alarm was dismissed."""
        if not self.ringing:
            return False

        self._ring_stop.set()
        if self._ring_thread and self._ring_thread.is_alive():
            self._ring_thread.join(timeout=3)
        self._ring_thread = None

        dismissed = self._ringing_alarm
        self.ringing = False
        self._ringing_alarm = None

        # Resume music if it was paused
        self.event_system.publish(EventMessage("system", "music_resume", {}))

        if dismissed:
            logger.info(f"Alarm dismissed: {dismissed.description}")

        return True

    def _check_alarms(self, event: EventMessage):
        """Check and trigger expired alarms on each system tick."""
        now = datetime.now()

        with self._lock:
            expired = [a for a in self.alarms if a.alarm_time <= now]
            self.alarms = [a for a in self.alarms if a.alarm_time > now]

        for alarm in expired:
            logger.info(f"Alarm expired: {alarm.description}")
            self._start_ringing(alarm)

            # Notify the LLM
            self.event_system.publish(EventMessage(
                "tool",
                "alarm",
                f"Alarm triggered: {alarm.description}. The alarm is ringing and will continue until the user says stop, cancel, or silence.",
                process_output=True
            ))
            # Only ring for the first expired alarm
            break
