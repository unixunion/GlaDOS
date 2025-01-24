import dataclasses
import threading
import time
from datetime import datetime
from typing import List, Optional
import dateparser

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem, EventMessage
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()


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
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

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
            process_output=True
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
            process_output=True
        )(self.get_alarms)

    def start(self):
        logger.info("Starting Alarm Clock System...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def ticker():
            while not self._stop_event.is_set():
                self.check_alarms()
                time.sleep(1)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=ticker, daemon=True)
        self._worker_thread.start()
        logger.success("Alarm Clock System started")

    def stop(self):
        logger.info("Stopping Alarm Clock System...")
        self._stop_event.set()

    def set_fixed_time_alarm(self, time: str, description: Optional[str] = None):
        """
        Register a new fixed-time alarm and add it to the list.

        :param time: The time for the alarm in natural language format (e.g., '5pm tomorrow').
        :param description: Optional description for the alarm.
        :return: A success or error message.
        """
        try:
            if not time:
                return {"status": "error", "message": "The 'time' field is required for setting an alarm."}

            # Parse natural language time
            alarm_time = dateparser.parse(time)
            if not alarm_time:
                return {"status": "error", "message": f"Could not parse the time: '{time}'."}

            if alarm_time < datetime.now():
                return {"status": "error", "message": "Cannot set an alarm for a past time."}

            with self._lock:
                # Prevent duplicate alarms
                for alarm in self.alarms:
                    if alarm["time"] == alarm_time:
                        return {"status": "error", "message": "An alarm is already set for this time."}

                # Add the alarm
                self.alarms.append(Alarm(alarm_time=alarm_time, description=description or None))
                return {
                    "status": "success",
                    "message": f"Alarm set for {alarm_time.strftime('%Y-%m-%d %H:%M:%S')}.",
                    "description": description or "No description provided",
                }
        except Exception as e:
            logger.exception(f"Error setting fixed-time alarm: {str(e)}")
            return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}

    def get_alarms(self):
        """
        Retrieve all currently set alarms.

        :return: A list of alarms with their times and descriptions.
        """
        with self._lock:
            if not self.alarms:
                return {"status": "success", "message": "No alarms are currently set.", "alarms": []}

            alarms_list = [
                {"time": alarm.alarm_time.strftime('%Y-%m-%d %H:%M:%S'), "description": alarm.description or None}
                for alarm in self.alarms
            ]
            return {"status": "success", "message": "Currently set alarms:", "alarms": alarms_list}

    def check_alarms(self):
        """Check alarms and trigger expired ones."""
        now = datetime.now()
        expired_alarms = []

        with self._lock:
            expired_alarms = [alarm for alarm in self.alarms if alarm["time"] <= now]
            self.alarms = [alarm for alarm in self.alarms if alarm["time"] > now]

        for alarm in expired_alarms:
            self.event_system.publish(EventMessage(
                "tool",
                "alarm",
                f"Alarm triggered: {alarm['description']} at {alarm['time'].strftime('%Y-%m-%d %H:%M:%S')}",
                process_output=True
            ))
