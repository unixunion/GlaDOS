import json
import queue
import threading
import time

from loguru import logger
from openai import OpenAI
from mistralai.client import Mistral
from langchain_ollama import ChatOllama

from glados.config import GladosConfig
from glados.context.activity import Activity
from glados.llm.client_type import ClientType
from glados.llm.event_manager import EventHandler
from glados.llm.message_manager import MessageManager
from glados.llm.response_processor import ResponseProcessor
from glados.llm.stream_handler import StreamHandler
from glados.llm.tool_executor import ToolExecutor
from glados.system.event_system import EventSystem, EventHook, EventMessage
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()
event_system = EventSystem()


class ChatClient:
    def __init__(self, config: GladosConfig):
        self.client = None
        self.model = config.model
        self.plugin_system = PluginSystem()
        self.config: GladosConfig = config
        self.llm_queue: queue.Queue[str] = queue.Queue()
        self.tts_queue: queue.Queue[str] = queue.Queue()
        self.message_manager = MessageManager(
            max_context_messages=getattr(config, 'max_context_messages', 20)
        )
        self.client_type = None

        for line in config.personality_preprompt:
            role = list(line.keys())[0]
            content = list(line.values())[0]
            logger.info(f"System prompt ({role}): {str(content)[:80]}...")
            for activity in Activity:
                self.message_manager.add_message(role, content, activity=activity)

        # Append plugin-registered system prompt additions
        plugin_prompts = self.plugin_system.get_system_prompts()
        if plugin_prompts:
            combined = "\n".join(plugin_prompts)
            logger.info(f"Appending {len(plugin_prompts)} plugin system prompt(s) to all {len(list(Activity))} activity contexts")
            for activity in Activity:
                self.message_manager.add_message("system", combined, activity=activity)

        if config.client_type.upper() == ClientType.OPENAI.name:
            self.client = OpenAI(base_url=config.completion_url, api_key=config.api_key)
            self.client_type = ClientType.OPENAI
        elif config.client_type.upper() == ClientType.MISTRAL.name:
            self.client = Mistral(server_url=config.completion_url, api_key=config.api_key)
            self.client_type = ClientType.MISTRAL
        elif config.client_type.upper() == ClientType.LANGCHAIN.name:
            tools = self.plugin_system.get_available_tools(architecture=ClientType.LANGCHAIN)
            logger.debug(f"langchain tools: {tools}")
            self.client = ChatOllama(
                model=config.model,
                temperature=0,
                base_url=config.completion_url,
                seed=42
            ).bind_tools(tools)
            self.client_type = ClientType.LANGCHAIN
        else:
            raise ValueError(f"Unsupported client type: {config.client_type}")
        logger.success(f"{config.client_type.upper()} client created: {self.client}")

        self.stream_handler = StreamHandler(self.client, self.model, self.message_manager, config)
        logger.success(f"StreamHandler created: {self.stream_handler}")
        self.tool_executor = ToolExecutor(plugin_manager=self.plugin_system)
        logger.success(f"ToolExecutor created: {self.tool_executor}")

        # Pass the message manager as a callback to the response processor
        self.response_processor = ResponseProcessor(
            tts_queue=self.tts_queue,
            message_callback=self._store_message_in_history,
            client_type=self.client_type
        )
        logger.success(f"ResponseProcessor created: {self.response_processor}")

        self.event_handler = EventHandler(self.message_manager)
        logger.success(f"EventHandler created: {self.event_handler}")
        self._setup_event_subscriptions()
        self.shutdown_event = threading.Event()
        self._llm_thread = threading.Thread(target=self._process_llm_queue, daemon=True)
        self._llm_thread.start()
        logger.success("ChatClient initialized successfully! Ready to process messages.")

    def start(self):
        """
        Start the LLM queue processing thread.
        """
        logger.info("Starting LLM queue processing thread...")
        self._llm_thread.start()

    def stop(self):
        """
        Stop the LLM queue processing thread.
        """
        logger.info("Stopping LLM queue processing thread...")
        self.shutdown_event.set()
        self._llm_thread.join()

    def _process_llm_queue(self):
        """
        Monitor the LLM queue and process messages.
        """
        while not self.shutdown_event.is_set():
            try:
                # Retrieve user input from the queue
                user_input = self.llm_queue.get(timeout=0.1)
                if user_input:
                    # Drain any stale queued messages — if the LLM was slow,
                    # the user may have repeated themselves, piling up garbage
                    # transcriptions. Keep only the latest message.
                    latest = user_input
                    drained = 0
                    while not self.llm_queue.empty():
                        try:
                            latest = self.llm_queue.get_nowait()
                            drained += 1
                        except queue.Empty:
                            break
                    if drained > 0:
                        logger.info(f"Drained {drained} stale message(s) from LLM queue, using latest")

                    logger.info(f"Processing input from LLM queue: {latest[:100]}")
                    event_system.publish(EventMessage("status", "thinking", {"message": "Thinking..."}))
                    self.chat(latest, tools=None)
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"Error processing LLM queue: {e}")
            time.sleep(0.1)

    def _setup_event_subscriptions(self):
        """Set up subscriptions to the event system."""
        event_system.subscribe(
            "tool.*",
            EventHook(name="tool_event_handler", callback=self._handle_tool_event, priority=1)
        )
        logger.debug("Subscribed to tool events.")

        event_system.subscribe(
            "system.tick",
            EventHook(name="tick_handler", callback=self._handle_tick, priority=1)
        )

        event_system.subscribe(
            "vision.response",
            EventHook(name="vision_response_handler", callback=self.handle_vision_response)
        )

    def handle_vision_response(self, event: EventMessage):
        """Handle vision responses."""
        description = event.content.get("description", "No description provided.")

        inferred_activity = self.infer_activity_from_input(description)
        self.message_manager.switch_context(inferred_activity)

        additional_prompt = event.content.get("prompt", "The following image description comes from a camera "
                                                        "surveillance image classifier, examine the image for any "
                                                        "hazards and respond accordingly")
        logger.info(f"Received vision response: {description}")
        # Incorporate the vision response into the chat
        response = f"{additional_prompt}, vision model description of image: {description}"
        logger.info(f"image response {response.strip()}")
        self.message_manager.add_message_to_current_context("tool", response)
        self.chat(additional_prompt,
                  tools=plugin_manager.get_available_tools(architecture=self.client_type))  # tells the llm to run again
        logger.info(f"triggered LLM to process results")

    def _handle_tool_event(self, event: EventMessage):
        """Handle events from tools."""
        if event.process_output:
            # If process_output is True, send the event content to the LLM
            logger.info(f"Processing tool event via LLM: {str(event.content)[:128]}")
            self.llm_queue.put(str(event.content))
        else:
            logger.info(f"Adding tool event to history: {str(event.content)[:128]}")
            self.message_manager.add_message("tool", event.content)

    def _handle_tick(self, event: EventMessage):
        logger.debug(f"Received tick: {event}")

    def _store_message_in_history(self, message: str):
        """Callback to store finalized sentences in the message manager."""
        logger.debug(f"Storing finalized message in history: {message}")
        self.message_manager.add_message_to_current_context("assistant", message)

    def infer_activity_from_input(self, user_input: str) -> Activity:
        """Infer the activity based on user input using the intent classifier.

        Uses the intent classifier to predict which tool the user likely wants,
        then looks up that tool's registered activity category.
        Falls back to Activity.GENERAL if no confident match.
        """
        try:
            classifier = self.plugin_system.get_intent_classifier()
            if not classifier or not classifier.model:
                return Activity.GENERAL

            predicted_tool, confidence = classifier.predict_intent(user_input)
            if confidence < 0.3 or not predicted_tool:
                logger.debug(f"Activity inference: low confidence ({confidence:.2f}), using GENERAL")
                return Activity.GENERAL

            # Look up the tool's registered activity
            plugin_data = self.plugin_system.plugins.get(predicted_tool)
            if plugin_data and "activity" in plugin_data:
                activities = plugin_data["activity"]
                if activities:
                    activity = activities[0]
                    logger.info(f"Activity inferred: {activity} from tool '{predicted_tool}' (confidence: {confidence:.2f})")
                    return activity

        except Exception as e:
            logger.warning(f"Activity inference failed: {e}")

        return Activity.GENERAL

    def chat(self, content, tools=None, _recursive=False):
        """
        Handles user input and communicates with the LLM.
        """

        logger.debug(f"chat content: {content}, tools: {tools}")

        if content:
            inferred_activity = self.infer_activity_from_input(content)
            self.message_manager.switch_context(inferred_activity)
            event_system.publish(EventMessage(
                "status", "activity",
                {"activity": inferred_activity.name}
            ))

        if content:  # Add input to the conversation
            self.message_manager.add_message_to_current_context("user", str(content))

        relevant_tools = plugin_manager.get_available_tools(
            architecture=self.client_type,
            activity=self.message_manager.current_context
        )
        logger.debug(f"Proposed relevant_tools: {[t.get('function', {}).get('name', '?') if isinstance(t, dict) else str(t)[:40] for t in relevant_tools]}")

        model_to_use = self.model

        try:
            response = self.stream_handler.stream_response(relevant_tools or tools, model=model_to_use, query=content)
            logger.debug(f"response back from stream handler: {response}")

            # Accumulate streamed tool call deltas into complete tool calls
            # OpenAI streams tool calls in fragments: first chunk has id+name,
            # subsequent chunks only have argument pieces
            pending_tool_calls = {}  # index -> {id, name, arguments}

            for chunk in response:
                logger.debug(f"chunk: {chunk}")
                if self.config.client_type.upper() == ClientType.OPENAI.name:
                    logger.debug("using openai client type")
                    if chunk.choices[0].delta.tool_calls:
                        for tc_delta in chunk.choices[0].delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in pending_tool_calls:
                                pending_tool_calls[idx] = {"id": None, "name": None, "arguments": ""}
                            if tc_delta.id:
                                pending_tool_calls[idx]["id"] = tc_delta.id
                            if tc_delta.function and tc_delta.function.name:
                                pending_tool_calls[idx]["name"] = tc_delta.function.name
                            if tc_delta.function and tc_delta.function.arguments:
                                pending_tool_calls[idx]["arguments"] += tc_delta.function.arguments
                    else:
                        self.response_processor.process_chunk(chunk)
                elif self.config.client_type.upper() == ClientType.LANGCHAIN.name:
                    logger.debug("using langchain client type")
                    if chunk.tool_calls:
                        logger.info(f"tool calls: {chunk.tool_calls}")
                        for tool_call in chunk.tool_calls:
                            # Adapt LangChain tool call to your format
                            # adapted_tool_call = {
                            #     "function": {
                            #         "name": tool_call["name"],  # Extract name from LangChain tool call
                            #         "arguments": json.dumps(tool_call.get("args", {}))  # Convert args to JSON string
                            #     }
                            # }

                            # Execute the tool
                            tool_result = self.tool_executor.execute_tool(tool_call,
                                                                          architecture=self.client_type)

                            # Add the tool result to the message context
                            self.message_manager.add_message_to_current_context(
                                "tool", str(tool_result), name=tool_call["name"]
                            )

                            # Check if tool result needs further processing
                            process_tool_result = plugin_manager.should_process_plugin_output(tool_call["name"])
                            logger.debug(f"Should process tool output: {process_tool_result}")

                            if process_tool_result:
                                self.chat(None, tools=None, _recursive=True)  # Recursive call to continue conversation
                            else:
                                logger.info("Not appending tool output to messages. Sending it directly to TTS.")
                                self.llm_queue.put(str(tool_result))

                    else:
                        logger.info("no tool calls")
                        self.response_processor.process_chunk(chunk)

            # Execute accumulated tool calls (OpenAI streaming)
            if pending_tool_calls:
                for idx in sorted(pending_tool_calls.keys()):
                    tc = pending_tool_calls[idx]
                    if not tc["name"]:
                        logger.warning(f"Skipping tool call at index {idx} with no name")
                        continue
                    logger.info(f"Executing accumulated tool call: {tc['name']} with args: {tc['arguments']}")
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse tool arguments: {tc['arguments']}")
                        continue

                    function_to_call = self.plugin_system.get_available_llm_functions().get(tc["name"])
                    if not function_to_call:
                        logger.warning(f"Tool {tc['name']} not found, skipping")
                        continue

                    try:
                        result = function_to_call(**args) if args else function_to_call()
                        tool_result = {"tool": tc["name"], "result": result}
                    except Exception as e:
                        logger.exception(f"Error executing tool {tc['name']}: {e}")
                        tool_result = {"error": str(e), "tool": tc["name"]}

                    process_tool_result = plugin_manager.should_process_plugin_output(tc["name"])
                    logger.debug(f"tool process_output: {process_tool_result}")
                    self.message_manager.add_message_to_current_context("tool", str(tool_result), name=tc["name"])

                    if process_tool_result:
                        self.chat(None, tools=None, _recursive=True)
                    else:
                        logger.info("Tool output added to messages, sending to TTS via queue")
                        self.llm_queue.put(str(tool_result))

            # Finalize any remaining sentence fragment
            self.response_processor.finalize_sentence()

            # Only the top-level call emits EOS and stores the response —
            # recursive calls (after tool execution) must not duplicate these.
            if not _recursive:
                self.tts_queue.put("<EOS>")
                self.response_processor.finalize_response()
        except Exception as e:
            logger.exception(f"Chat error: {e}")

    def is_tts_queue_empty(self):
        """
        Check if the TTS queue is empty.
        """
        return self.tts_queue.empty()
