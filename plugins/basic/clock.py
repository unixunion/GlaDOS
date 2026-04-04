import json
from datetime import datetime

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool


def _format_time_response(result: str) -> str:
    try:
        data = json.loads(result)
        return f"The time is {data['time']}."
    except Exception:
        return f"The time is {result}."


@mcp_tool(
    description="The current time and date",
    intents=[
        "What is the time",
        "what is the time right now",
        "tell me the time",
        "tell me the time please",
        "what time is it",
        "what time is it now",
        "What is the date",
        "what is today's date",
        "time please",
        "do you have the time",
        "can you tell me the time",
        "give me the current time",
        "what is the current time",
        "check the time",
        "clock",
        "What's the time?",
    ],
    process_output=True,
    activity=[Activity.SYSTEM, Activity.GENERAL, Activity.COOKING, Activity.UTILITIES],
    nlp_response=_format_time_response,
    nlp_threshold=0.6,
)
def get_current_time() -> str:
    """
    Returns the current date and time
    """
    now = datetime.now()
    formatted = now.strftime("%H:%M")
    t = json.dumps({"time": formatted})
    logger.info(f"Get Current Date: {t}")
    return t
