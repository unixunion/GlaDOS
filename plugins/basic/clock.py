import json
from datetime import datetime

from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="The current time and date",
                                             parameters=Parameters(type="object", required=[], properties={}),
                                         )
                                         ),
    intents=[
        "What is the time",
        "tell me the time please",
        "What is the date",
        "time please",
    ],
    process_output=True,
    activity=[Activity.SYSTEM, Activity.GENERAL, Activity.COOKING, Activity.UTILITIES]
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
