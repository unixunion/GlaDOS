import json
from datetime import datetime

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool


@mcp_tool(
    description="The current time and date",
    intents=[
        "What is the time",
        "tell me the time please",
        "What is the date",
        "time please",
    ],
    process_output=True,
    activity=[Activity.SYSTEM, Activity.GENERAL, Activity.COOKING, Activity.UTILITIES],
)
def get_current_time() -> str:
    """
    Returns the current date and time
    """
    now = datetime.now()
    formatted = now.strftime("%H:%M:%S")
    t = json.dumps({"time": formatted})
    logger.info(f"Get Current Date: {t}")
    return t
