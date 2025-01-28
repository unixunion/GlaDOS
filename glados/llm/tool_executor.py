import inspect
import json

from loguru import logger

from glados.llm.client_type import ClientType
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem


class ToolExecutor:
    def __init__(self, plugin_manager: PluginSystem = None):
        self.plugin_manager = plugin_manager
        self.event_system = EventSystem()

    def execute_tool(self, tool_call, architecture=ClientType.OPENAI) -> dict:

        tools_to_call = [tool_call]
        results = []

        if architecture is ClientType.LANGCHAIN and tool_call:
            if isinstance(tool_call, list):
                tools_to_call.extend(tool_call)  # Extend the list with items
            else:
                tools_to_call.append(tool_call)  # Append the single item

        for tool in tools_to_call:
            logger.info(f"calling tool: {tool}")

            if architecture is not ClientType.LANGCHAIN:
                logger.info("its a openai tool, so using propreties")
                function_name = tool.function.name
                arguments = tool.function.arguments
            elif architecture is ClientType.LANGCHAIN:
                logger.info("its a langchain tool, so using dictionary")
                function_name = tool['name']
                arguments = tool['args']
            else:
                logger.warning("Unknown tool, giving up")
                return {"error": f"unknown tool: {tool}"}

            if not (function_to_call := self.plugin_manager.get_available_llm_functions().get(function_name)):
                logger.warning(f"Tool {function_name} not found.")
                return {"error": f"Tool {function_name} not found."}

            try:
                logger.info(f"Executing tool: {function_name} with args: {arguments}")
                if architecture is ClientType.OPENAI:
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
                    results.append({"tool": function_name, "result": result})
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
                return {"error": str(e), "tool": f"{function_name}"}

        logger.info(f"returning results: {results}")
        return {"status": "success", "results": results}
