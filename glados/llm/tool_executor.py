import inspect
import json
import random
import string
from typing import Callable

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

    def process_streamed_tool_calls(
        self,
        pending_tool_calls: dict,
        message_manager,
        called_tools: set,
        depth: int,
        max_depth: int,
        chat_callback: Callable,
        llm_queue=None,
    ) -> None:
        """Execute accumulated tool calls from OpenAI streaming responses.

        Builds the assistant tool_calls message, executes each tool, publishes
        display events, adds results to message history, and triggers recursive
        chat callbacks for tools that need LLM summarization.

        Args:
            pending_tool_calls: Dict keyed by index with {id, name, arguments} per tool call.
            message_manager: MessageManager for adding messages to conversation history.
            called_tools: Set of already-called tool names (for repeat-call guard).
            depth: Current recursion depth.
            max_depth: Maximum allowed recursion depth.
            chat_callback: Callable to invoke for tools with process_output=True.
            llm_queue: Optional queue for tools with process_output=False (sends result to LLM queue).
        """
        if depth >= max_depth:
            logger.warning(f"[Recursion Guard] Ignoring tool calls at depth {depth}: "
                           f"{[tc.get('name') for tc in pending_tool_calls.values()]}")
            return

        logger.info(f"Tool call at depth {depth}: {[tc.get('name') for tc in pending_tool_calls.values()]}")

        # Store the assistant message with tool_calls array
        # Generate 9-char alphanumeric IDs for compatibility (Mistral requires [a-zA-Z0-9]{9})
        assistant_tool_calls = []
        for idx in sorted(pending_tool_calls.keys()):
            tc = pending_tool_calls[idx]
            if tc["name"]:
                tc["id"] = ''.join(random.choices(string.ascii_letters + string.digits, k=9))
                assistant_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"}
                })
        if assistant_tool_calls:
            message_manager.add_message_to_current_context(
                "assistant", None, tool_calls=assistant_tool_calls
            )

        for idx in sorted(pending_tool_calls.keys()):
            tc = pending_tool_calls[idx]
            if not tc["name"]:
                logger.warning(f"Skipping tool call at index {idx} with no name")
                continue
            logger.info(f"Executing accumulated tool call: {tc['name']} with args: {tc['arguments']}")

            # Block repeat calls to the same tool in this exchange
            if tc["name"] in called_tools:
                logger.warning(f"[Recursion Guard] Skipping repeat call to '{tc['name']}' (already called in this exchange)")
                continue
            called_tools.add(tc["name"])

            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                logger.error(f"Failed to parse tool arguments: {tc['arguments']}")
                continue

            # Emit tool call to chat panel
            self.event_system.publish(EventMessage(
                "chat", "tool_call", {"role": "tool_call", "tool": tc["name"], "args": args}
            ))

            function_to_call = self.plugin_manager.get_available_llm_functions().get(tc["name"])
            if not function_to_call:
                logger.warning(f"Tool {tc['name']} not found, skipping")
                continue

            try:
                result = function_to_call(**args) if args else function_to_call()
                tool_result = {"tool": tc["name"], "result": result}
            except Exception as e:
                logger.exception(f"Error executing tool {tc['name']}: {e}")
                tool_result = {"error": str(e), "tool": tc["name"]}

            # Emit tool result to chat panel (format nicely)
            result_content = tool_result.get("result", tool_result)
            if isinstance(result_content, dict):
                display_parts = []
                if result_content.get("status"):
                    display_parts.append(f"Status: {result_content['status']}")
                if result_content.get("message"):
                    display_parts.append(result_content["message"])
                if result_content.get("passages"):
                    display_parts.append(str(result_content["passages"])[:200])
                result_display = "\n".join(display_parts) if display_parts else str(result_content)[:300]
            else:
                result_display = str(result_content)[:300]
            self.event_system.publish(EventMessage("chat", "tool_result", {
                "role": "tool_result",
                "tool": tc["name"],
                "content": result_display,
            }))

            process_tool_result = self.plugin_manager.should_process_plugin_output(tc["name"])
            logger.debug(f"tool process_output: {process_tool_result}")
            # Truncate tool results to prevent context bloat
            result_str = str(tool_result)
            if len(result_str) > 1000:
                result_str = result_str[:1000] + "... [truncated]"
            message_manager.add_message_to_current_context(
                "tool", result_str, name=tc["name"], tool_call_id=tc["id"]
            )

            if process_tool_result:
                chat_callback()
            elif llm_queue:
                logger.info("Tool output added to messages, sending to LLM queue")
                llm_queue.put(str(tool_result))

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
