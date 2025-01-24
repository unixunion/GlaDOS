import dataclasses
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.event_system.event_system import EventSystem, EventMessage, EventHook
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()


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


class CountdownTimer(RunnablePlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            logger.info("Instantiating singleton")
            cls._instance = super(CountdownTimer, cls).__new__(cls)
            cls._instance._initialized = False  # Ensure this is only done once
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()
        self.timers: List[Timer] = []
        self.event_system = EventSystem()
        logger.info("CountdownTimer system initializing.")

        # Register LLM functions
        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Set a timer for a specified duration and an optional description.",
                    parameters=Parameters(
                        type="object",
                        properties={
                            "hours": ParameterType(type="integer", description="Number of hours for the timer."),
                            "minutes": ParameterType(type="integer", description="Number of minutes for the timer."),
                            "seconds": ParameterType(type="integer", description="Number of seconds for the timer."),
                            "description": ParameterType(type="string",
                                                         description="Optional description for the timer."),
                        },
                        required=[],
                        additionalProperties=False,
                    ),
                ),
            ),
            intents=[
                "Set a timer for twelve minutes",
                "Start a countdown timer for 45 seconds",
                "Set a timer for 1 hour and 2 minutes",
                "Timer for 60 seconds"
            ],
            process_output=True,
        )(self.set_timer)



        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="List all active timers with their descriptions and expiration times.",
                    parameters=Parameters(type="object", properties={}, required=[], additionalProperties=False),
                ),
            ),
            intents=[
                "list all timers",
                "what timers are active",
                "how much time left on my egg timer"
            ],
            process_output=True,
        )(self.list_timers)

    def add_timer(self, alarm_time: datetime, description: str):
        """
        Add a new timer to the list.
        """

        self.timers.append(Timer(
            alarm_time=alarm_time,
            description=description
        ))

        logger.info(f"Added timer: {description}, expires at {format_time_for_tts(alarm_time)}.")

    def remove_expired_timers(self):
        """
        Remove expired timers and return them for further processing.
        """
        now = datetime.now()
        expired_timers = [timer for timer in self.timers if timer.alarm_time <= now]
        self.timers = [timer for timer in self.timers if timer.alarm_time > now]
        return expired_timers

    def set_timer(self,
                  hours: Optional[int] = 0,
                  minutes: Optional[int] = 0,
                  seconds: Optional[int] = 0,
                  description: Optional[str] = None):
        """
        Set a timer for a specific duration.
        """
        try:
            # Validate duration
            total_seconds = timedelta(
                hours=int(hours or 0),
                minutes=int(minutes or 0),
                seconds=int(seconds or 0)
            ).total_seconds()
            if total_seconds <= 0:
                return {"status": "error", "message": "Duration must be greater than 0 seconds."}

            # Calculate alarm time
            alarm_time = datetime.now() + timedelta(seconds=total_seconds)
            duration_str = format_duration(int(total_seconds))

            # Generate default description
            description = description or f"the {duration_str} timer"

            # Add the timer
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
        """
        List all active timers with their descriptions and expiration durations.
        """
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
        """
        Hook to check and handle expired timers.
        """
        logger.debug("Checking timers")
        expired_timers = self.remove_expired_timers()

        for timer in expired_timers:
            logger.info(f"Timer called: '{timer.description}' has expired, firing event")

            self.event_system.publish(
                EventMessage(
                    role="tool",
                    name="set_timer",
                    content={
                        "message": f"A timer called: '{timer.description}' has expired."
                    },
                    process_output=True
                )
            )

    def start(self):
        logger.info("Starting CountdownTimer.")
        self.event_system.subscribe("system.tick", EventHook("check_timers", callback=self._check_timers, priority=5))
        # self.event_system.register_hook(self._check_timers)

    def stop(self):
        logger.info("Stopping CountdownTimer.")
        # self.event_system.unregister_hook(self._check_timers)

# class CountdownTimer(RunnablePlugin):
#
#     def __init__(self):
#         super().__init__()
#         self.timers = []
#         logger.info("CountdownTimer system initializing.")
#
#         self.event_system = EventSystem()
#
#         plugin_manager.register(
#             llm_function_request=FunctionRequest(
#                 type="function",
#                 function=FunctionMetadata(
#                     description="Set an a timer for a duration of either hours, minutes or seconds or a combination "
#                                 "of these. Also takes a description for the timer",
#                     parameters=Parameters(
#                         type="object",
#                         properties={
#                             "hours": ParameterType(
#                                 type="integer",
#                                 description="Number of hours for the timer."
#                             ),
#                             "minutes": ParameterType(
#                                 type="integer",
#                                 description="Number of minutes for the timer.",
#                             ),
#                             "seconds": ParameterType(
#                                 type="integer",
#                                 description="Number of seconds for the timer."
#                             ),
#                             "description": ParameterType(
#                                 type="string",
#                                 description="Optional description for the timer"
#                             ),
#                         },
#                         required=[],
#                         additionalProperties=False
#                     )
#                 )),
#             process_output=False
#         )(self.set_timer)
#
#         plugin_manager.register(
#             llm_function_request=FunctionRequest(
#                 type="function",
#                 function=FunctionMetadata(
#                     description="List all active timers with their descriptions and expiration times.",
#                     parameters=Parameters(
#                         type="object",
#                         properties={},
#                         required=[],
#                         additionalProperties=False
#                     )
#                 )),
#             process_output=False
#         )(self.list_timers)
#
#     def set_timer(
#             self,
#             hours: Optional[int] = 0,
#             minutes: Optional[int] = 0,
#             seconds: Optional[int] = 0,
#             description: Optional[str] = None
#     ):
#         """
#         Set a timer for a specific duration.
#
#         Args:
#             hours (int): Number of hours for the timer.
#             minutes (int): Number of minutes for the timer.
#             seconds (int): Number of seconds for the timer.
#             description (str): Optional description for the timer.
#
#         Returns:
#             Dict: A status message about the timer.
#         """
#         try:
#             # Validate and calculate total duration in seconds
#             total_seconds = timedelta(
#                 hours=int(hours) or 0,
#                 minutes=int(minutes) or 0,
#                 seconds=int(seconds) or 0
#             ).total_seconds()
#
#             if total_seconds <= 0:
#                 return {"status": "error", "message": "Duration must be greater than 0 seconds."}
#
#             # Calculate the alarm time
#             alarm_time = datetime.now() + timedelta(seconds=total_seconds)
#
#             # Generate a default description if none is provided
#             if not description:
#                 description = f"the {_format_duration(int(total_seconds))} timer"
#
#             # Store the timer
#             self.timers.append({
#                 "time": alarm_time,
#                 "description": description,
#                 "duration": _format_duration(int(total_seconds))
#             })
#
#             logger.info(f"Timer set for {description}, expires in {_format_duration(int(total_seconds))}.")
#             return {
#                 "status": "done",
#                 "message": f"Timer '{description}' set for {_format_duration(int(total_seconds))}.",
#                 "expires_in": _format_duration(int(total_seconds))
#             }
#
#         except Exception as e:
#             logger.error(f"Error setting timer: {e}")
#             return {"status": "error", "message": str(e)}
#
#     def list_timers(self):
#         """
#         List all active timers with their descriptions and expiration durations.
#         """
#         if not self.timers:
#             return {"status": "empty", "message": "No active timers."}
#
#         now = datetime.now()
#         timers_info = []
#         for timer in self.timers:
#             remaining_time = (timer["time"] - now).total_seconds()
#             timers_info.append({
#                 "description": timer["description"],
#                 "expires_in": _format_duration(int(remaining_time)),
#             })
#
#         return {
#             "status": "success",
#             "timers": timers_info
#         }
#
#     def _check_timers(self, *args, **kwargs):
#         """
#         Hook to check and handle expired timers.
#         """
#         logger.info("Hook called, checking timers")
#         now = datetime.now()
#         expired_timers = [timer for timer in self.timers if timer["time"] <= now]
#         self.timers = [timer for timer in self.timers if timer["time"] > now]
#
#         for timer in expired_timers:
#             logger.info(f"Timer expired: {timer['description']}, firing event")
#             self.event_system.add_event(EventMessage(
#                 role="tool",
#                 name="set_timer",
#                 content={
#                     "message": f"A timer has expired: {timer['description']}",
#                     "trigger_time": timer["time"].isoformat()
#                 },
#                 process_output=False
#             ),
#                 originating_hook=self._check_timers,
#             )
#
#     def start(self):
#         logger.info("Registering event system hooks")
#         self.event_system.register_hook(self._check_timers)
#
#     def stop(self):
#         logger.info("Unregistering event system hooks")
#         self.event_system.unregister_hook(self._check_timers)
