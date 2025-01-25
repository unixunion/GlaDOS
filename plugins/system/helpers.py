from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Lists all plugins, integrations and functions currently "
                                                         "loaded into the home assistant architecture. This information "
                                                         "should be summarized",
                                             parameters=Parameters(type="object", required=[], properties={}),
                                         )
                                         ),
    intents=[
        "list all plugins",
        "what are your functions",
        "list all functions",
        "what are your capabilities",
        "tell me about your integrations"
    ],
    activity=[Activity.SYSTEM]
)
def list_plugins() -> dict:
    try:
        plugin_list = []
        for name, plugin in plugin_manager.plugins.items():
            description = plugin.get("description", "No description available.")
            readable_name = f"{name.replace('_', ' ').capitalize()}"
            plugin_list.append({'name': readable_name, 'description': description})
        return {'status': 'success',
                'plugins': plugin_list
                }
    except Exception as e:
        logger.error(f"Unable to list plugins, error was {e}")
        return {'status': 'error', 'cause': f"{e}"}
