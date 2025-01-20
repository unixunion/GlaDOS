from datetime import time, timedelta, datetime
from typing import List, Optional, Dict

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem
from plugins.plugin_manager import PluginManager

plugin_manager = PluginManager()


class AlarmClock:
    def __init__(self, event_system: EventSystem):
        self.alarms: List[float] = []  # Timestamps for when alarms should trigger
        self.event_system = event_system

    @plugin_manager.register(
        "set_alarm",
        "Set an alarm or reminder",
        FunctionRequest(
            type="function",
            function=FunctionMetadata(
                name="set_alarm",
                description="Sets an alarm, timer, or reminder.",
                parameters=Parameters(
                    type="object",
                    properties={
                        "time": ParameterType(
                            type="string",
                            description="The time for the alarm in HH:MM format."
                        ),
                        "duration": ParameterType(
                            type="object",
                            description="Specify duration for the alarm.",
                        ),
                        "description": ParameterType(
                            type="string",
                            description="Optional description for the alarm."
                        )
                    },
                    required=["time"],
                    additionalProperties=False
                )
        )).to_dict(),
        process_output=False,
    )
    def set_alarm(self, time: Optional[str] = None, duration: Optional[Dict[str, int]] = None, description: str = None):
        """
        Set an alarm to go off at the specified time or after a duration.

        Args:
            time (str): Time in HH:MM format (optional).
            duration (dict): Duration in hours, minutes, and seconds (optional).
            description (str): Optional description for the alarm.
        """
        try:
            if not time and not duration:
                return {"status": "error", "message": "Either 'time' or 'duration' must be provided."}

            # Calculate alarm time
            if time:
                alarm_time = datetime.strptime(time, "%H:%M").replace(
                    year=datetime.now().year, month=datetime.now().month, day=datetime.now().day
                )
                if alarm_time < datetime.now():  # Adjust for next day
                    alarm_time += timedelta(days=1)
            else:
                total_seconds = timedelta(
                    hours=duration.get("hours", 0),
                    minutes=duration.get("minutes", 0),
                    seconds=duration.get("seconds", 0)
                ).total_seconds()
                if total_seconds <= 0:
                    return {"status": "error", "message": "Duration must be greater than 0 seconds."}
                alarm_time = datetime.now() + timedelta(seconds=total_seconds)

            # Store alarm
            self.alarms.append({"time": alarm_time, "description": description})
            return {
                "status": "success",
                "message": f"Alarm set for {alarm_time.strftime('%Y-%m-%d %H:%M:%S')}.",
                "description": description or "No description provided"
            }

        except Exception as e:
            return {"status": "error", "message": f"Failed to set alarm: {str(e)}"}

    def check_alarms(self):
        """Check if any alarms have expired and trigger events."""
        now = datetime.now()
        expired_alarms = [alarm for alarm in self.alarms if alarm["time"] <= now]
        self.alarms = [alarm for alarm in self.alarms if alarm["time"] > now]

        for alarm in expired_alarms:
            self.event_system.add_event({
                "type": "alarm",
                "time": alarm["time"].strftime('%Y-%m-%d %H:%M:%S'),
                "description": alarm["description"] or "No description",
                "message": "Alarm triggered!"
            })