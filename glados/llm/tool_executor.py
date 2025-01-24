import inspect
import json

from loguru import logger

from plugins.event_system.event_system import EventSystem, EventMessage
from plugins.plugin_system.plugin_manager import PluginManager


class ToolExecutor:
    def __init__(self, plugin_manager: PluginManager = None):
        self.plugin_manager = plugin_manager
        self.event_system = EventSystem()

    def execute_tool(self, tool_call):
        function_name = tool_call.function.name
        arguments = tool_call.function.arguments

        if not (function_to_call := self.plugin_manager.get_available_llm_functions().get(function_name)):
            logger.warning(f"Tool {function_name} not found.")
            return {"error": f"Tool {function_name} not found."}

        try:
            logger.info(f"Executing tool: {function_name} with args: {arguments}")
            args = json.loads(arguments) if arguments else {}

            sig = inspect.signature(function_to_call)
            parameters = sig.parameters
            result = None
            if not parameters:
                result = function_to_call()
            elif not arguments:
                raise ValueError(
                    f"Function {function_to_call.__name__} expects arguments but none were provided.")
            else:
                result = function_to_call(**args)
            return {"result": result}
        except Exception as e:
            logger.exception(f"Error executing tool {function_name}: {e}")
            self.event_system.publish(EventMessage(
                role="tool",
                name=f"{function_name}",
                content=f"There was a error invoking this tool, the error was: {e}",
                process_output=True
            ))
            self.event_system.publish(EventMessage(
                role="log",
                name=f"{function_name}",
                content=f"There was a error invoking this tool, the error was: {e}"
            ))
            return {"error": str(e)}
