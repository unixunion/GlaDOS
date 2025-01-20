import json
from datetime import datetime

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters
from plugins.plugin_manager import PluginManager

plugin_manager = PluginManager()


@plugin_manager.register(
    "get_current_time",
    "gets the current date and time",
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='get_current_time',
                        description="The current time and date",
                        parameters=Parameters(type="object", required=[], properties={}),
                    )
                    ).to_dict(),
    process_output=True
)
def get_current_time() -> int:
    """
    Returns the current date and time
    """
    now = datetime.now()
    formatted = now.strftime("%H:%M:%S")
    t = json.dumps({"time": formatted})
    logger.info(f"Get Current Date: {t}")
    return t



