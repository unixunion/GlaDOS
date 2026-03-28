"""ChatClient — central orchestrator for the GlaDOS chat pipeline.

Creates and wires together all LLM components: backend, stream handler,
tool executor, response processor, message manager, event handlers,
and queue processor.

The chat() method is the main entry point for processing user input.
"""

import queue
import threading
import time
import uuid

from loguru import logger

from glados.config import GladosConfig
from glados.context.activity import Activity
from glados.llm.backends import LLMBackend
from glados.llm.client_type import ClientType
from glados.llm.cores.event_handlers import ChatEventHandlers
from glados.llm.cores.queue_processor import LLMQueueProcessor
from glados.llm.event_manager import EventHandler
from glados.llm.message_manager import MessageManager
from glados.llm.response_processor import ResponseProcessor
from glados.llm.stream_handler import StreamHandler
from glados.llm.tool_executor import ToolExecutor
from glados.system.event_system import EventSystem, EventMessage
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()
event_system = EventSystem()


class ChatClient:
    def __init__(self, config: GladosConfig, plugin_system=None, event_system_instance=None):
        self.model = config.model
        self.plugin_system = plugin_system or PluginSystem()
        self.config: GladosConfig = config
        self.llm_queue: queue.Queue[str] = queue.Queue()
        self.tts_queue: queue.Queue[str] = queue.Queue()
        self.message_manager = MessageManager(
            max_context_messages=getattr(config, 'max_context_messages', 20)
        )
        self.client_type = ClientType[config.client_type.upper()] if not getattr(config, 'nlp_mode', False) else ClientType.OPENAI
        self._nlp_dispatcher = None
        self.backend = None
        self._from_display = False

        # Load personality preprompt into all activity contexts
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

        # Create LLM backend via factory
        if getattr(config, 'nlp_mode', False):
            logger.info("NLP mode enabled — skipping LLM backend creation")
        else:
            self.backend = LLMBackend.from_config(config)
            logger.success(f"{config.client_type.upper()} backend created")

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

        # Create pipeline components
        self.stream_handler = None
        self.tool_executor = None
        self.response_processor = None

        if not getattr(config, 'nlp_mode', False):
            self.stream_handler = StreamHandler(self.backend, self.model, self.message_manager, config)
            logger.success(f"StreamHandler created")
            self.tool_executor = ToolExecutor(plugin_manager=self.plugin_system)
            logger.success(f"ToolExecutor created")
            self.response_processor = ResponseProcessor(
                tts_queue=self.tts_queue,
                message_callback=self._store_message_in_history,
                buffer_mode=getattr(config, 'tts_buffer_mode', 'clause'),
                word_buffer=getattr(config, 'tts_word_buffer', 5),
            )
            logger.success(f"ResponseProcessor created")

        # Event handling (extracted)
        self.event_handler_obj = EventHandler(self.message_manager)
        self._event_handlers = ChatEventHandlers(self)
        self._event_handlers.setup()

        # Queue processing (extracted)
        self.shutdown_event = threading.Event()
        self._queue_processor = LLMQueueProcessor(self.llm_queue, self.chat, self.shutdown_event)
        self._queue_processor.start()
        logger.success("ChatClient initialized successfully!")

    def load_plugin_prompts(self):
        """Inject plugin-registered system prompts into all activity contexts."""
        plugin_prompts = self.plugin_system.get_system_prompts()
        if plugin_prompts:
            combined = "\n".join(plugin_prompts)
            logger.info(f"Appending {len(plugin_prompts)} plugin system prompt(s)")
            for activity in Activity:
                self.message_manager.add_message("system", combined, activity=activity)

    def start(self):
        """Start the LLM queue processing thread."""
        pass  # Queue processor started in __init__

    def stop(self):
        """Stop the LLM queue processing thread."""
        logger.info("Stopping ChatClient...")
        self.shutdown_event.set()
        self._queue_processor.join()

    def _store_message_in_history(self, message: str):
        """Callback to store finalized sentences in the message manager."""
        logger.debug(f"Storing finalized message in history: {message}")
        self.message_manager.add_message_to_current_context("assistant", message)
        if self._memory_store and self._last_user_message and getattr(self.config, 'memory_auto_store', False):
            try:
                self._memory_store.store_exchange(
                    user_message=self._last_user_message,
                    assistant_message=message,
                    activity=self.message_manager.current_context.name,
                    session_id=self._session_id,
                )
                self._last_user_message = None
            except Exception as e:
                logger.warning(f"[Memory] Failed to store exchange: {e}")

    def infer_activity_from_input(self, user_input: str) -> Activity:
        """Infer the activity based on user input using the intent classifier."""
        try:
            classifier = self.plugin_system.get_intent_classifier()
            if not classifier or not classifier.model:
                return Activity.GENERAL

            predicted_tool, confidence = classifier.predict_intent(user_input)
            if confidence < 0.3 or not predicted_tool:
                return Activity.GENERAL

            plugin_data = self.plugin_system.plugins.get(predicted_tool)
            if plugin_data and "activity" in plugin_data:
                activities = plugin_data["activity"]
                if activities:
                    activity = activities[0]
                    logger.info(f"Activity inferred: {activity} from tool '{predicted_tool}' (confidence: {confidence:.2f})")
                    return activity

            from glados.nlp.handler import NLPHandlerRegistry
            nlp_handler = NLPHandlerRegistry().get(predicted_tool)
            if nlp_handler and nlp_handler.activity:
                activity = nlp_handler.activity[0]
                logger.info(f"Activity inferred: {activity} from NLP handler '{predicted_tool}' (confidence: {confidence:.2f})")
                return activity

        except Exception as e:
            logger.warning(f"Activity inference failed: {e}")

        return Activity.GENERAL

    # -----------------------------------------------------------------------
    # Stream consumption (normalized via StreamChunk)
    # -----------------------------------------------------------------------

    def _consume_stream(self, response, _depth, _called_tools) -> dict:
        """Consume the normalized LLM response stream, returning accumulated tool calls."""
        pending_tool_calls = {}
        _response_start = None
        _max_response_time = getattr(self.config, 'max_response_time', 15)

        for chunk in response:
            if _response_start and time.monotonic() - _response_start > _max_response_time:
                logger.warning(f"[Loop Guard] Response exceeded {_max_response_time}s wall-clock limit, aborting stream")
                while not self.tts_queue.empty():
                    try:
                        self.tts_queue.get_nowait()
                    except Exception:
                        break
                break

            if chunk.tool_call_deltas:
                if not _response_start:
                    _response_start = time.monotonic()
                for tc_delta in chunk.tool_call_deltas:
                    idx = tc_delta.index
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"id": None, "name": None, "arguments": ""}
                    if tc_delta.id:
                        pending_tool_calls[idx]["id"] = tc_delta.id
                    if tc_delta.name:
                        pending_tool_calls[idx]["name"] = tc_delta.name
                    if tc_delta.arguments:
                        pending_tool_calls[idx]["arguments"] += tc_delta.arguments

            elif chunk.content:
                if not _response_start:
                    _response_start = time.monotonic()
                self.response_processor.process_chunk(chunk)

        return pending_tool_calls

    # -----------------------------------------------------------------------
    # Main chat entry point
    # -----------------------------------------------------------------------

    def chat(self, content, tools=None, _recursive=False, _depth=0, _called_tools=None):
        """Handles user input and communicates with the LLM."""
        if _called_tools is None:
            _called_tools = set()

        _max_depth = getattr(self.config, 'max_tool_depth', 3)
        if _depth >= _max_depth:
            logger.info(f"[Recursion Guard] Depth {_depth} >= max {_max_depth} — text-only response")
            tools = None

        if content:
            inferred_activity = self.infer_activity_from_input(content)
            self.message_manager.switch_context(inferred_activity)
            event_system.publish(EventMessage("status", "activity", {"activity": inferred_activity.name}))

        if content:
            self.message_manager.add_message_to_current_context("user", str(content))
            from_display = getattr(self, '_from_display', False)
            self._from_display = False
            if not from_display and not str(content).startswith("You have just been powered on"):
                event_system.publish(EventMessage("chat", "user", {"role": "user", "content": str(content)}))

        # -- Pre-LLM hooks --
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
            self._chat_ctx = ctx

        # -- Hybrid NLP fast-path --
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

        # -- NLP mode --
        if self.config.nlp_mode and self._nlp_dispatcher and content and not _recursive:
            self._nlp_dispatcher.dispatch(str(content), self.message_manager.current_context)
            return

        # -- Tool override from hooks (e.g., shopping sub-context) --
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

        try:
            response = self.stream_handler.stream_response(
                relevant_tools or tools, model=self.model, query=content, memory_context=memory_context
            )

            pending_tool_calls = self._consume_stream(response, _depth, _called_tools)

            if pending_tool_calls:
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

            self.response_processor.finalize_sentence()

            if not _recursive:
                self.tts_queue.put("<EOS>")
                self.response_processor.finalize_response()
                if hasattr(self, '_chat_ctx') and self._chat_ctx:
                    from glados.llm.chat_hooks import ChatHookRegistry, ChatPipelinePhase
                    ChatHookRegistry().run_hooks(ChatPipelinePhase.POST_RESPONSE, self._chat_ctx)
                    self._chat_ctx = None
        except Exception as e:
            logger.exception(f"Chat error: {e}")

    def is_tts_queue_empty(self):
        return self.tts_queue.empty()
