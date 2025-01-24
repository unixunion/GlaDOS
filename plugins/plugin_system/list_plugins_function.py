from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters
from plugins.plugin_system.plugin_manager import PluginManager

plugin_manager = PluginManager()


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Lists all plugins, integrations and functions currently "
                                                         "loaded into the home assistant architecture",
                                             parameters=Parameters(type="object", required=[], properties={}),
                                         )
                                         ),
    intents=[
        "list all plugins",
        "what are your functions",
        "list all functions",
        "what are your capabilities",
        "tell me about your integrations"
    ]

)
def list_plugins() -> str:
    try:
        plugin_list = []
        for name, plugin in plugin_manager.plugins.items():
            description = plugin.get("description", "No description available.")
            readable_name = f"{name.replace('_', ' ').capitalize()}"
            plugin_list.append(f"{readable_name}: {description}")
        return "\n".join(plugin_list)
    except Exception as e:
        logger.error(f"Unable to list plugins, error was {e}")
