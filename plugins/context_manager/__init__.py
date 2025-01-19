from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParamaterType
from plugins.context_manager.context_manager import ContextManager
from plugins.plugin_manager import PluginManager

from loguru import logger

plugin_manager = PluginManager()
context_manager = ContextManager()

switch_context_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='switch_context',
                        description="Switches or changes the current activity to another activity by name",
                        parameters=Parameters(type="object", required=['activity_name'], properties={
                            'activity_name': ParamaterType(type="string", description="the activity to switch to")
                        })
                    )
                    )
).to_dict()


@plugin_manager.register(
    "switch_context",
    "Switches to another activity by name",
    switch_context_definition
)
def switch_context(activity_name: str) -> str:
    try:
        context_manager.switch_to_activity(activity_name)
    except Exception as e:
        logger.error(f"Unable to switch to activity: {activity_name}, error was {e}")



# Exit Activity
exit_activity_definition = FunctionRequest(
    type="function",
    function=FunctionMetadata(
        name="exit_activity",
        description="Exits the currently active activity.",
        parameters=Parameters(type="object", required=[], properties={}),
    ),
).to_dict()


@plugin_manager.register(
    "exit_activity",
    "Exits the currently active activity.",
    exit_activity_definition,
)
def exit_activity() -> str:
    try:
        context_manager.switch_to_activity("general")
        return "Exited the current activity successfully."
    except Exception as e:
        logger.error(f"Error exiting activity, error was {e}")
        return "Error: Could not exit the current activity."


# List Activities
list_activities_definition = FunctionRequest(
    type="function",
    function=FunctionMetadata(
        name="list_activities",
        description="Lists all available activities, contexts and tasks.",
        parameters=Parameters(type="object", required=[], properties={}),
    ),
).to_dict()


@plugin_manager.register(
    "list_activities",
    "Lists all available activities.",
    list_activities_definition,
)
def list_activities() -> list:
    try:
        return list(context_manager.contexts.keys())
    except Exception as e:
        logger.error(f"Error listing activities, error was {e}")
        return []


# Get Context of an Activity
get_activity_context_definition = FunctionRequest(
    type="function",
    function=FunctionMetadata(
        name="get_activity_context",
        description="Retrieves the context of a specific activity by name.",
        parameters=Parameters(
            type="object",
            required=["activity_name"],
            properties={
                "activity_name": ParamaterType(
                    type="string",
                    description="The name of the activity whose context is to be retrieved.",
                ),
            },
        ),
    ),
).to_dict()


@plugin_manager.register(
    "get_activity_context",
    "Retrieves the context of a specific activity by name.",
    get_activity_context_definition,
)
def get_activity_context(activity_name: str) -> str:
    try:
        return context_manager.get_task_context(activity_name)
    except Exception as e:
        logger.error(f"Unable to retrieve context for activity '{activity_name}', error was {e}")
        return f"Error: Could not retrieve context for activity '{activity_name}'."


# Delete Activity
delete_activity_definition = FunctionRequest(
    type="function",
    function=FunctionMetadata(
        name="delete_activity",
        description="Deletes a specific activity by name.",
        parameters=Parameters(
            type="object",
            required=["activity_name"],
            properties={
                "activity_name": ParamaterType(
                    type="string",
                    description="The name of the activity to delete.",
                ),
            },
        ),
    ),
).to_dict()


@plugin_manager.register(
    "delete_activity",
    "Deletes a specific activity by name.",
    delete_activity_definition,
)
def delete_activity(activity_name: str) -> str:
    try:
        if activity_name in context_manager.contexts:
            del context_manager.contexts[activity_name]
            return f"Activity '{activity_name}' deleted successfully."
        else:
            return f"Activity '{activity_name}' does not exist."
    except Exception as e:
        logger.error(f"Error deleting activity '{activity_name}', error was {e}")
        return f"Error: Could not delete activity '{activity_name}'."
