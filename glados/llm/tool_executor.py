import inspect
import json

from loguru import logger

from glados.llm.client_type import ClientType
from glados.mcp.server import GladosMCPServer
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem


class ToolExecutor:
    def __init__(self, plugin_manager: PluginSystem = None):
        self.plugin_manager = plugin_manager
        self.event_system = EventSystem()
        self.mcp_server = GladosMCPServer()

    def execute_tool(self, tool_call, architecture=ClientType.OPENAI) -> dict:

        results = []

        if architecture is ClientType.LANGCHAIN and tool_call:
            if isinstance(tool_call, list):
                tools_to_call = tool_call
            else:
                tools_to_call = [tool_call]
        else:
            tools_to_call = [tool_call]

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
                self.event_system.publish(EventMessage(
                    "status", "tool_call", {"message": f"Using {function_name}", "tool": function_name}
                ))
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

    def execute_tool_via_mcp(self, function_name: str, arguments: dict) -> dict:
        """Execute a tool through the MCP server.

        This is the MCP-native execution path. Falls back to the legacy
        PluginSystem path if the tool isn't registered with MCP.
        """
        if not self.mcp_server.has_tool(function_name):
            logger.debug(f"Tool '{function_name}' not in MCP server, falling back to legacy execution")
            return None

        self.event_system.publish(EventMessage(
            "status", "tool_call", {"message": f"Using {function_name}", "tool": function_name}
        ))

        result = self.mcp_server.call_tool(function_name, arguments)

        if result.isError:
            error_text = result.content[0].text if result.content else "Unknown error"
            logger.error(f"MCP tool '{function_name}' returned error: {error_text}")
            self.event_system.publish(EventMessage(
                role="tool", name=function_name,
                content=f"There was an error invoking this tool, the error was: {error_text}",
                process_output=True
            ))
            return {"error": error_text, "tool": function_name}

        # Extract text from MCP CallToolResult
        result_text = result.content[0].text if result.content else ""
        try:
            result_data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            result_data = result_text

        return {"status": "success", "results": [{"tool": function_name, "result": result_data}]}
