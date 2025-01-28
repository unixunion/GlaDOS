import json
import queue
import threading
import time

from loguru import logger
from openai import OpenAI
from mistralai import Mistral
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
        self.message_manager = MessageManager()
        self.client_type = None

        for line in config.personality_preprompt:
            logger.info(f"role: {list(line.keys())[0]}, content: {list(line.values())[0]}")
            self.message_manager.add_message(list(line.keys())[0], list(line.values())[0])

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
                    tools = plugin_manager.get_available_tools(architecture=self.client_type) or []
                    logger.info(f"Processing input from LLM queue: {user_input}, tools: {tools}")
                    self.chat(user_input, tools=tools)
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
        logger.info("Subscribed to role: tool events.")

        event_system.subscribe(
            "system.tick",
            EventHook(name="tick_handler", callback=self._handle_tick, priority=1)
        )
        logger.info("Subscribed to role: tick events.")

        event_system.subscribe(
            "vision.response",
            EventHook(name="vision_response_handler", callback=self.handle_vision_response)
        )
        logger.info("Subscribed to vision system")

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
        logger.info(f"Tool event received: {str(event)[0:128]}")

        if event.process_output:
            # If process_output is True, send the event content to the LLM
            logger.info(f"Processing tool event via LLM: {event.content}")
            self.llm_queue.put(str(event.content))
        else:
            # Otherwise, just add it to the message history
            logger.info(f"Adding tool event to message history: {event.content}")
            self.message_manager.add_message("tool", event.content)

    def _handle_tick(self, event: EventMessage):
        logger.debug(f"Received tick: {event}")

    def _store_message_in_history(self, message: str):
        """Callback to store finalized sentences in the message manager."""
        logger.debug(f"Storing finalized message in history: {message}")
        self.message_manager.add_message_to_current_context("assistant", message)

    def infer_activity_from_input(self, user_input: str) -> Activity:
        """Infer the activity based on user input."""
        logger.info("Determine activity, somehow,... returning GENERAL now as a stub")
        return Activity.GENERAL

    def chat(self, content, tools=None):
        """
        Handles user input and communicates with the LLM.
        """

        logger.debug(f"chat content: {content}, tools: {tools}")

        if content:
            inferred_activity = self.infer_activity_from_input(content)
            self.message_manager.switch_context(inferred_activity)

        if content:  # Add input to the conversation
            self.message_manager.add_message_to_current_context("user", str(content))

        relevant_tools = plugin_manager.get_available_tools(
            architecture=self.client_type,
            activity=self.message_manager.current_context
        )
        logger.info(f"Proposed relevant_tools: {relevant_tools}")

        model_to_use = self.model

        try:
            response = self.stream_handler.stream_response(tools, model=model_to_use, query=content)
            logger.debug(f"response back from stream handler: {response}")
            for chunk in response:
                logger.debug(f"chunk: {chunk}")
                if self.config.client_type.upper() == ClientType.OPENAI.name:
                    logger.debug("using openai client type")
                    if chunk.choices[0].delta.tool_calls:
                        logger.debug("tool calls in chunk")
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            tool_result = self.tool_executor.execute_tool(tool_call)
                            process_tool_result = plugin_manager.should_process_plugin_output(tool_call.function.name)
                            logger.info(f"tool process_output: {process_tool_result}")
                            self.message_manager.add_message_to_current_context("tool", str(tool_result),
                                                                                name=tool_call.function.name)
                            if process_tool_result:
                                self.chat(None, tools=None)
                            else:
                                logger.info(
                                    "Not appending tool output to the messages, but sending it direct to the TTS, "
                                    "the model will know about it if queried though, since its added to the messages")
                                self.llm_queue.put(str(tool_result))
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
                            logger.info(f"Should process tool output: {process_tool_result}")

                            if process_tool_result:
                                self.chat(None, tools=None)  # Recursive call to continue conversation
                            else:
                                logger.info("Not appending tool output to messages. Sending it directly to TTS.")
                                self.llm_queue.put(str(tool_result))

                    else:
                        logger.info("no tool calls")
                        self.response_processor.process_chunk(chunk)

            # Finalize any remaining sentence
            self.response_processor.finalize_sentence()
            self.tts_queue.put("<EOS>")

            # Store the assistant's complete response
            if self.response_processor.current_sentence.strip():
                logger.info(f"Final assistant message: {self.response_processor.current_sentence}")
                self.message_manager.add_message_to_current_context("assistant",
                                                                    self.response_processor.current_sentence)
            else:
                logger.warning("Assistant response is empty before adding to MessageManager!, this can probably be "
                               "ignored once confirmed that there is no loss of data")
        except Exception as e:
            logger.exception(f"Chat error: {e}")

    def is_tts_queue_empty(self):
        """
        Check if the TTS queue is empty.
        """
        return self.tts_queue.empty()
