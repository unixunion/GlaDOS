import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem, EventMessage
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()


class AlarmClock(RunnablePlugin):

    def __init__(self):
        super().__init__()
        logger.info("Instantiating Alarm Clock System")
        self.alarms: List[float] = []
        self.event_system = EventSystem()
        self._worker_thread = None
        self._stop_event = threading.Event()

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Sets an alarm at a fixed time (HH:MM format).",
                    parameters=Parameters(
                        type="object",
                        properties={
                            "time": ParameterType(
                                type="string",
                                description="The fixed time for the alarm in HH:MM format (24-hour clock)."
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
                "set a alarm for seventeen thirty four",
                "set the alarm for 5 am tomorrow",
                "set an alarm for 7pm"
            ],
            process_output=False
        )(self.set_fixed_time_alarm)

    def start(self):
        logger.info("Starting...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def ticker():
            while not self._stop_event.is_set():
                logger.info("checking alarms")
                self.check_alarms()
                time.sleep(1)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=ticker, daemon=True)
        self._worker_thread.start()
        logger.success("started")

    def stop(self):
        logger.info("Shutting down")
        self._stop_event.set()

    def set_fixed_time_alarm(self, time: str, description: Optional[str] = None):
        """
        Register a new fixed-time alarm and add it to the list.

        :param time: The fixed time for the alarm in HH:MM format.
        :param description: Optional description for the alarm.
        :return: A success or error message.
        """
        try:
            if not time:
                return {"status": "error", "message": "The 'time' field is required for fixed-time alarms."}

            with self._lock:
                # Parse the provided time (assume "HH:MM" format)
                try:
                    if ":" in time:  # Handle "HH:MM" format
                        alarm_time = datetime.strptime(time, "%H:%M").replace(
                            year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
                        )
                        if alarm_time < datetime.now():  # Adjust for the next day if time has already passed
                            alarm_time += timedelta(days=1)
                    else:
                        return {"status": "error", "message": f"Invalid time format: {time}. Expected HH:MM format."}
                except ValueError:
                    return {"status": "error", "message": f"Failed to parse time: {time}. Check the format."}

                # Prevent duplicate alarms
                for alarm in self.alarms:
                    if alarm["time"] == alarm_time:
                        return {"status": "error", "message": "An alarm is already set for this time."}

                # Add the alarm
                self.alarms.append({"time": alarm_time, "description": description or "No description"})
                return {
                    "status": "success",
                    "message": f"Alarm set for {alarm_time.strftime('%Y-%m-%d %H:%M:%S')}.",
                    "description": description or "No description provided",
                }
        except Exception as e:
            logger.exception(f"Error setting fixed-time alarm: {str(e)}")
            raise e

    def check_alarms(self):
        """Check alarms and trigger expired ones."""
        now = datetime.now()
        expired_alarms = [alarm for alarm in self.alarms if alarm["time"] <= now]
        self.alarms = [alarm for alarm in self.alarms if alarm["time"] > now]

        for alarm in expired_alarms:
            self.event_system.publish(EventMessage(
                "tool",
                "alarm",
                "A scheduled alarm has gone off",
                process_output=True
            ))
