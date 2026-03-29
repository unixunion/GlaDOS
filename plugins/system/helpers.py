from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()
event_system = EventSystem()


def _format_plugins_response(result: dict) -> str:
    if result.get("status") == "error":
        return f"Error listing plugins: {result.get('cause', 'unknown')}"
    plugins = result.get("plugins", [])
    if not plugins:
        return "No plugins are loaded."
    names = [p["name"] for p in plugins]
    return f"I have {len(plugins)} tools loaded: {', '.join(names)}."


@mcp_tool(
    description="Lists all plugins, integrations and functions currently "
                "loaded into the home assistant architecture. This information "
                "should be summarized. The list is also automatically shown on the display.",
    intents=[
        "list all plugins",
        "what are your functions",
        "list all functions",
        "what are your capabilities",
        "tell me about your integrations",
        "what plugins are loaded",
        "list available tools",
        "what can you do",
        "show me all plugins on the display",
        "list plugins on the screen",
    ],
    process_output=True,
    activity=[Activity.SYSTEM],
    nlp_response=_format_plugins_response,
)
def list_plugins() -> dict:
    try:
        plugin_list = []
        for name, plugin in plugin_manager.plugins.items():
            description = plugin.get("description", "No description available.")
            readable_name = f"{name.replace('_', ' ').capitalize()}"
            plugin_list.append({'name': readable_name, 'description': description})

        # Auto-push to display
        content = "\n".join(f"- {p['name']}: {p['description']}" for p in plugin_list)
        event_system.publish(EventMessage(
            role="display",
            name="info",
            content={"title": "Loaded Plugins", "content": content},
            process_output=False,
        ))

        return {'status': 'success',
                'plugins': plugin_list
                }
    except Exception as e:
        logger.error(f"Unable to list plugins, error was {e}")
        return {'status': 'error', 'cause': f"{e}"}
