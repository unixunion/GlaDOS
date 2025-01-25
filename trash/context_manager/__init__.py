# import json
#
# from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
# from plugins.context_manager.context_manager import ContextManager
# from plugins.plugin_system.plugin_manager import PluginManager
#
# from loguru import logger
#
# plugin_manager = PluginManager()
# context_manager = ContextManager()
#
#
# @plugin_manager.register(
#     llm_function_request=FunctionRequest(
#         type="function",
#         function=FunctionMetadata(
#             description="Switches the current activity to a different one by specifying its name.",
#             parameters=Parameters(
#                 type="object",
#                 required=["activity_name"],
#                 properties={
#                     "activity_name": ParameterType(
#                         type="string",
#                         description="The exact name of the activity to switch to.",
#                         enum=["General", "Music", "Cooking", "Game", "Automation"],
#                     )
#                 },
#             ),
#         ),
#     )
# )
# def switch_context(activity_name: str) -> str:
#     try:
#         context_manager.switch_to_activity(activity_name)
#         return f"Context was switched to {activity_name}, continue"
#     except Exception as e:
#         logger.error(f"Unable to switch to activity: {activity_name}, error was {e}")
#
#
# # Exit Activity
# exit_activity_definition = FunctionRequest(
#     function=FunctionMetadata(
#         description="Exits the currently active activity and returns to the general context.",
#         parameters=Parameters(type="object", required=[], properties={}),
#     )
# )
#
#
# @plugin_manager.register(
#     llm_function_request=exit_activity_definition
# )
# def exit_activity() -> str:
#     try:
#         context_manager.switch_to_activity("general")
#         return "Exited the current activity successfully."
#     except Exception as e:
#         logger.error(f"Error exiting activity, error was {e}")
#         return "Error: Could not exit the current activity."
#
#
# # List Activities
# list_activities_definition = FunctionRequest(
#     type="function",
#     function=FunctionMetadata(
#         description="Lists all activities that are currently available for activity switching or interaction.",
#         parameters=Parameters(type="object", required=[], properties={}),
#     )
# )
#
#
# @plugin_manager.register(
#     llm_function_request=list_activities_definition
# )
# def list_activities() -> list:
#     try:
#         return list(context_manager.contexts.keys())
#     except Exception as e:
#         logger.error(f"Error listing activities, error was {e}")
#         return []
#
#
# # Get Context of an Activity
# get_activity_context_definition = FunctionRequest(
#     type="function",
#     function=FunctionMetadata(
#         description="Retrieves detailed information about the context of a specific activity by its name.",
#         parameters=Parameters(
#             type="object",
#             required=["activity_name"],
#             properties={
#                 "activity_name": ParameterType(
#                     type="string",
#                     description="The name of the activity whose context details should be retrieved, such as 'Cooking' or 'Music' or 'Trivia'",
#                 ),
#             },
#         ),
#     ),
# )
#
#
# @plugin_manager.register(
#     llm_function_request=get_activity_context_definition,
# )
# def get_activity_context(activity_name: str) -> str:
#     try:
#         # Switch to the requested activity
#         context_manager.switch_to_activity(activity_name)
#
#         # Retrieve the task context
#         context = context_manager.get_task_context()
#
#         # Return a JSON-formatted string with additional guidance
#         return json.dumps({
#             "status": "success",
#             "activity_name": activity_name,
#             "context": context,
#             "guidance": (
#                 "This context provides detailed information about the specified activity. "
#                 "Do not initiate a response unless directly prompted by the user. If the context is unclear, "
#                 "ask the user for clarification before proceeding."
#             ),
#         }, indent=4)
#     except Exception as e:
#         # Log the error and return a formatted error message
#         logger.error(f"Unable to retrieve context for activity '{activity_name}', error was {e}")
#         return json.dumps({
#             "status": "error",
#             "activity_name": activity_name,
#             "message": f"Could not retrieve context for activity '{activity_name}'.",
#             "details": str(e),
#         }, indent=4)
#
# #
# # # Delete Activity
# # delete_activity_definition = FunctionRequest(
# #     type="function",
# #     function=FunctionMetadata(
# #         name="delete_activity",
# #         description="Deletes a specific activity by name.",
# #         parameters=Parameters(
# #             type="object",
# #             required=["activity_name"],
# #             properties={
# #                 "activity_name": ParamaterType(
# #                     type="string",
# #                     description="The name of the activity to delete.",
# #                 ),
# #             },
# #         ),
# #     ),
# # ).to_dict()
# #
# #
# # @plugin_manager.register(
# #     "delete_activity",
# #     "Deletes a specific activity by name.",
# #     delete_activity_definition,
# # )
# # def delete_activity(activity_name: str) -> str:
# #     try:
# #         if activity_name in context_manager.contexts:
# #             del context_manager.contexts[activity_name]
# #             return f"Activity '{activity_name}' deleted successfully."
# #         else:
# #             return f"Activity '{activity_name}' does not exist."
# #     except Exception as e:
# #         logger.error(f"Error deleting activity '{activity_name}', error was {e}")
# #         return f"Error: Could not delete activity '{activity_name}'."
#
#
# # # Create Activity
# # create_activity_definition = FunctionRequest(
# #     type="function",
# #     function=FunctionMetadata(
# #         name="create_activity",
# #         description="Creates a new activity with the specified name and initializes its context.",
# #         parameters=Parameters(
# #             type="object",
# #             required=["activity_name"],
# #             properties={
# #                 "activity_name": ParamaterType(
# #                     type="string",
# #                     description="The name of the activity to create, such as 'Project Planning' or 'Daily Reflection'."
# #                 ),
# #             },
# #         ),
# #     ),
# # ).to_dict()
# #
# # @plugin_manager.register(
# #     "create_activity",
# #     "Create a activity.",
# #     create_activity_definition,
# # )
# # def create_activity(activity_name: str) -> str:
# #     try:
# #         if activity_name in context_manager.contexts:
# #             return f"Activity '{activity_name}' already exists."
# #         else:
# #             return f"Activity '{activity_name}' created."
# #     except Exception as e:
# #         logger.error(f"Error creating activity '{activity_name}', error was {e}")
# #         return f"Error: Could not create activity '{activity_name}'."
