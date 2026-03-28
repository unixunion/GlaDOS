import queue
import random
import string
import threading
import time
import uuid

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
        self._nlp_dispatcher = None

        for line in config.personality_preprompt:
            role = list(line.keys())[0]
            content = list(line.values())[0]
            logger.info(f"System prompt ({role}): {str(content)[:80]}...")
            for activity in Activity:
                self.message_manager.add_message(role, content, activity=activity)

        # Create NLP dispatcher for pure NLP mode OR hybrid mode
        if getattr(config, 'nlp_mode', False) or getattr(config, 'hybrid_nlp_threshold', 1.0) < 1.0:
            from glados.nlp.dispatcher import NLPDispatcher
            self._nlp_dispatcher = NLPDispatcher(
                tts_queue=self.tts_queue,
                confidence_threshold=getattr(config, 'nlp_confidence_threshold', 0.4),
            )

        if getattr(config, 'nlp_mode', False):
            logger.info("NLP mode enabled — skipping LLM client creation")
            self.client_type = ClientType.OPENAI  # placeholder for type checks
        elif config.client_type.upper() == ClientType.OPENAI.name:
            self.client = OpenAI(base_url=config.completion_url, api_key=config.api_key, timeout=20.0)
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
        if self.client:
            logger.success(f"{config.client_type.upper()} client created: {self.client}")

        # Memory system
        self._session_id = str(uuid.uuid4())
        self._memory_store = None
        self._last_user_message = None
        if getattr(config, 'memory_enabled', False):
            try:
                from glados.llm.memory.store import VectorMemoryStore
                self._memory_store = VectorMemoryStore(
                    db_path=config.memory_db_path,
                    top_k=config.memory_top_k,
                )
                logger.success("Vector memory store initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize memory store: {e}")

        self.stream_handler = None
        self.tool_executor = None
        self.response_processor = None

        if not getattr(config, 'nlp_mode', False):
            self.stream_handler = StreamHandler(self.client, self.model, self.message_manager, config)
            logger.success(f"StreamHandler created: {self.stream_handler}")
            self.tool_executor = ToolExecutor(plugin_manager=self.plugin_system)
            logger.success(f"ToolExecutor created: {self.tool_executor}")

            # Pass the message manager as a callback to the response processor
            self.response_processor = ResponseProcessor(
                tts_queue=self.tts_queue,
                message_callback=self._store_message_in_history,
                client_type=self.client_type,
                buffer_mode=getattr(config, 'tts_buffer_mode', 'clause'),
                word_buffer=getattr(config, 'tts_word_buffer', 5),
            )
            logger.success(f"ResponseProcessor created: {self.response_processor}")

        self.event_handler = EventHandler(self.message_manager)
        logger.success(f"EventHandler created: {self.event_handler}")
        self._setup_event_subscriptions()
        self.shutdown_event = threading.Event()
        self._llm_thread = threading.Thread(target=self._process_llm_queue, daemon=True)
        self._llm_thread.start()
        logger.success("ChatClient initialized successfully! Ready to process messages.")

    def load_plugin_prompts(self):
        """Inject plugin-registered system prompts into all activity contexts.
        Call after plugins have been loaded."""
        plugin_prompts = self.plugin_system.get_system_prompts()
        if plugin_prompts:
            combined = "\n".join(plugin_prompts)
            logger.info(f"Appending {len(plugin_prompts)} plugin system prompt(s) to all {len(list(Activity))} activity contexts")
            for activity in Activity:
                self.message_manager.add_message("system", combined, activity=activity)

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
            "tts.speak",
            EventHook(name="tts_speak_handler", callback=self._handle_tts_speak, priority=5)
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
            if event.name == "display_chat_input":
                # Display chat input — process as user message
                self._from_display = True
                logger.info(f"Processing tool event via LLM: {str(event.content)[:128]}")
                self.llm_queue.put(str(event.content))
            elif event.name not in ("plugin_system",):
                # Async plugin event (timer expired, alarm fired, etc.)
                # Pass as user-facing content so the LLM responds to it directly
                logger.info(f"Processing async tool event: {event.name} — {str(event.content)[:128]}")
                event_system.publish(EventMessage("chat", "async_event", {
                    "role": "async_event",
                    "source": event.name,
                    "content": str(event.content)[:200],
                }))
                # Extract clean event text
                event_text = str(event.content)
                if isinstance(event.content, dict) and "message" in event.content:
                    event_text = event.content["message"]

                # Inject as a proper tool call + result pair in the message history
                # This is the correct OpenAI format for async notifications
                call_id = ''.join(random.choices(string.ascii_letters + string.digits, k=9))
                self.message_manager.add_message_to_current_context(
                    "assistant", None, tool_calls=[{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": event.name, "arguments": "{}"}
                    }]
                )
                self.message_manager.add_message_to_current_context(
                    "tool", event_text, name=event.name, tool_call_id=call_id
                )
                # Trigger LLM to respond (no tools — just speak about the event)
                self.chat(None, tools=None, _recursive=True, _depth=1)
            else:
                logger.info(f"Processing tool event via LLM: {str(event.content)[:128]}")
                self.llm_queue.put(str(event.content))
        else:
            # Use "system" role for event-sourced messages — bare "tool" messages
            # without a preceding assistant tool_calls entry are invalid in the
            # OpenAI chat format and rejected by strict templates (e.g. Mistral).
            logger.info(f"Adding tool event to history as system message: {str(event.content)[:128]}")
            self.message_manager.add_message("system", event.content)

    def _handle_tts_speak(self, event: EventMessage):
        """Handle tts.speak events — send text directly to TTS without LLM processing."""
        text = str(event.content).strip()
        if text:
            logger.info(f"[TTS] Direct speak: {text[:100]}")
            self.tts_queue.put(text)
            self.tts_queue.put("<EOS>")

    def _handle_tick(self, event: EventMessage):
        logger.debug(f"Received tick: {event}")

    def _store_message_in_history(self, message: str):
        """Callback to store finalized sentences in the message manager."""
        logger.debug(f"Storing finalized message in history: {message}")
        self.message_manager.add_message_to_current_context("assistant", message)
        # Store the exchange in vector memory (only if auto_store enabled)
        if self._memory_store and self._last_user_message and getattr(self.config, 'memory_auto_store', False):
            try:
                logger.info(f"[Memory] Storing exchange — user: {self._last_user_message[:80]}...")
                logger.info(f"[Memory] Storing exchange — assistant: {message[:80]}...")
                self._memory_store.store_exchange(
                    user_message=self._last_user_message,
                    assistant_message=message,
                    activity=self.message_manager.current_context.name,
                    session_id=self._session_id,
                )
                self._last_user_message = None
            except Exception as e:
                logger.warning(f"[Memory] Failed to store exchange: {e}")
        elif self._memory_store and not self._last_user_message:
            logger.debug("[Memory] Skipping store — no pending user message (likely a recursive/tool call)")

    # -- Memory intent detection (pre-LLM) ------------------------------------
    #
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

            # Look up the tool's registered activity — check plugin system first,
            # then NLP handler registry (for NLP-only commands like cooking context)
            plugin_data = self.plugin_system.plugins.get(predicted_tool)
            if plugin_data and "activity" in plugin_data:
                activities = plugin_data["activity"]
                if activities:
                    activity = activities[0]
                    logger.info(f"Activity inferred: {activity} from tool '{predicted_tool}' (confidence: {confidence:.2f})")
                    return activity

            # Check NLP handler registry for NLP-only tools (e.g. cooking context)
            from glados.nlp.handler import NLPHandlerRegistry
            nlp_handler = NLPHandlerRegistry().get(predicted_tool)
            if nlp_handler and nlp_handler.activity:
                activity = nlp_handler.activity[0]
                logger.info(f"Activity inferred: {activity} from NLP handler '{predicted_tool}' (confidence: {confidence:.2f})")
                return activity

        except Exception as e:
            logger.warning(f"Activity inference failed: {e}")

        return Activity.GENERAL

    def _consume_stream(self, response, _depth, _called_tools) -> dict:
        """Consume the LLM response stream, returning accumulated tool calls.

        Handles both OpenAI and LangChain streaming. For OpenAI, tool call deltas
        are accumulated into a dict keyed by index. For LangChain, tool calls are
        executed inline during streaming (returns empty dict).

        Returns:
            dict: Pending tool calls keyed by index (OpenAI), or empty dict (LangChain).
        """
        pending_tool_calls = {}
        _response_start = None
        _max_response_time = getattr(self.config, 'max_response_time', 15)

        for chunk in response:
            # Wall-clock timeout guard — only starts counting after first visible output
            if _response_start and time.monotonic() - _response_start > _max_response_time:
                logger.warning(f"[Loop Guard] Response exceeded {_max_response_time}s wall-clock limit, aborting stream")
                while not self.tts_queue.empty():
                    try:
                        self.tts_queue.get_nowait()
                    except Exception:
                        break
                break
            logger.debug(f"chunk: {chunk}")
            if self.config.client_type.upper() == ClientType.OPENAI.name:
                if chunk.choices[0].delta.tool_calls:
                    if not _response_start:
                        _response_start = time.monotonic()
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
                    if not _response_start and chunk.choices[0].delta.content:
                        _response_start = time.monotonic()
                    self.response_processor.process_chunk(chunk)
            elif self.config.client_type.upper() == ClientType.LANGCHAIN.name:
                if chunk.tool_calls:
                    logger.info(f"tool calls: {chunk.tool_calls}")
                    for tool_call in chunk.tool_calls:
                        tool_result = self.tool_executor.execute_tool(tool_call,
                                                                      architecture=self.client_type)
                        self.message_manager.add_message_to_current_context(
                            "tool", str(tool_result), name=tool_call["name"]
                        )
                        process_tool_result = plugin_manager.should_process_plugin_output(tool_call["name"])
                        if process_tool_result:
                            self.chat(None, tools=None, _recursive=True, _depth=_depth + 1, _called_tools=_called_tools)
                        else:
                            logger.info("Not appending tool output to messages. Sending it directly to TTS.")
                            self.llm_queue.put(str(tool_result))
                else:
                    self.response_processor.process_chunk(chunk)

        return pending_tool_calls

    def chat(self, content, tools=None, _recursive=False, _depth=0, _called_tools=None):
        """
        Handles user input and communicates with the LLM.
        """
        if _called_tools is None:
            _called_tools = set()

        logger.debug(f"chat content: {content}, tools: {tools}, depth: {_depth}, called: {_called_tools}")

        # Prevent infinite recursive tool call loops
        _max_depth = getattr(self.config, 'max_tool_depth', 3)
        if _depth >= _max_depth:
            logger.info(f"[Recursion Guard] Depth {_depth} >= max {_max_depth} — text-only response")
            tools = None

        if content:
            inferred_activity = self.infer_activity_from_input(content)
            self.message_manager.switch_context(inferred_activity)
            event_system.publish(EventMessage(
                "status", "activity",
                {"activity": inferred_activity.name}
            ))

        if content:  # Add input to the conversation
            self.message_manager.add_message_to_current_context("user", str(content))
            # Emit chat.user only for voice input (display already rendered the bubble locally)
            from_display = getattr(self, '_from_display', False)
            self._from_display = False
            if not from_display and not str(content).startswith("You have just been powered on"):
                event_system.publish(EventMessage(
                    "chat", "user", {"role": "user", "content": str(content)}
                ))

        # -- Pre-LLM hooks (memory, future plugins) --------------------------------
        memory_context = None
        if content and not _recursive:
            from glados.llm.chat_hooks import ChatHookRegistry, ChatPipelinePhase, ChatContext
            ctx = ChatContext(
                user_text=str(content),
                activity=self.message_manager.current_context,
                session_id=self._session_id,
                tts_queue=self.tts_queue,
            )
            ChatHookRegistry().run_hooks(ChatPipelinePhase.PRE_LLM, ctx)
            if ctx.handled:
                return
            memory_context = ctx.memory_context
            self._chat_ctx = ctx  # Preserve for POST_RESPONSE hooks

        # -- Hybrid NLP fast-path: high-confidence tool execution bypasses LLM ------
        if (not _recursive and content and self._nlp_dispatcher
                and not self.config.nlp_mode
                and getattr(self.config, 'hybrid_nlp_threshold', 1.0) < 1.0):
            result = self._nlp_dispatcher.try_hybrid_dispatch(
                str(content), self.message_manager.current_context,
                self.config.hybrid_nlp_threshold,
            )
            if result.handled:
                return
            if result.tool_result is not None:
                self.message_manager.add_message_to_current_context(
                    "tool", str(result.tool_result), name=result.tool_name
                )
                self.chat(None, tools=None, _recursive=True, _depth=_depth + 1, _called_tools=_called_tools)
                return

        # -- NLP mode: bypass LLM entirely (only when nlp_mode=true, NOT hybrid) ---
        if self.config.nlp_mode and self._nlp_dispatcher and content and not _recursive:
            # Memory intents are handled by the PRE_LLM hook above (would have returned)
            self._nlp_dispatcher.dispatch(str(content), self.message_manager.current_context)
            return

        # Check if a PRE_LLM hook wants to restrict the tool set (e.g. shopping sub-context)
        tool_override = None
        if hasattr(self, '_chat_ctx') and self._chat_ctx and self._chat_ctx.extra.get("tool_override"):
            tool_override = self._chat_ctx.extra["tool_override"]

        if tool_override is not None:
            relevant_tools = tool_override
        else:
            relevant_tools = plugin_manager.get_available_tools(
                architecture=self.client_type,
                activity=self.message_manager.current_context
            )
        logger.debug(f"Proposed relevant_tools: {[t.get('function', {}).get('name', '?') if isinstance(t, dict) else str(t)[:40] for t in relevant_tools]}")

        model_to_use = self.model

        try:
            response = self.stream_handler.stream_response(relevant_tools or tools, model=model_to_use, query=content, memory_context=memory_context)
            logger.debug(f"response back from stream handler: {response}")

            pending_tool_calls = self._consume_stream(response, _depth, _called_tools)

            # Execute accumulated tool calls (OpenAI streaming)
            if pending_tool_calls:
                # Clear the response processor buffer so text isn't double-counted,
                # but let already-queued TTS sentences finish speaking.
                if self.response_processor:
                    self.response_processor.current_sentence = ""
                    self.response_processor.full_response = ""
                self.tool_executor.process_streamed_tool_calls(
                    pending_tool_calls, self.message_manager, _called_tools,
                    _depth, _max_depth,
                    chat_callback=lambda: self.chat(
                        None, tools=None, _recursive=True,
                        _depth=_depth + 1, _called_tools=_called_tools
                    ),
                    llm_queue=self.llm_queue,
                )

            # Finalize any remaining sentence fragment
            self.response_processor.finalize_sentence()

            # Only the top-level call emits EOS and stores the response —
            # recursive calls (after tool execution) must not duplicate these.
            if not _recursive:
                self.tts_queue.put("<EOS>")
                self.response_processor.finalize_response()
                # Run POST_RESPONSE hooks AFTER response is stored in history
                if hasattr(self, '_chat_ctx') and self._chat_ctx:
                    from glados.llm.chat_hooks import ChatHookRegistry, ChatPipelinePhase
                    ChatHookRegistry().run_hooks(ChatPipelinePhase.POST_RESPONSE, self._chat_ctx)
                    self._chat_ctx = None
        except Exception as e:
            logger.exception(f"Chat error: {e}")

    def is_tts_queue_empty(self):
        """
        Check if the TTS queue is empty.
        """
        return self.tts_queue.empty()
