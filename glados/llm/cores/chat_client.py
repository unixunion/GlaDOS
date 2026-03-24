import json
import queue
import random
import re
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

        if getattr(config, 'nlp_mode', False):
            logger.info("NLP mode enabled — skipping LLM client creation")
            self.client_type = ClientType.OPENAI  # placeholder for type checks
            from glados.nlp.dispatcher import NLPDispatcher
            self._nlp_dispatcher = NLPDispatcher(
                tts_queue=self.tts_queue,
                confidence_threshold=getattr(config, 'nlp_confidence_threshold', 0.4),
            )
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
            # If process_output is True, send the event content to the LLM
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
        # Store the exchange in vector memory
        if self._memory_store and self._last_user_message:
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
    # Intent detection uses the IntentClassifier (same Naive Bayes classifier
    # used for tool routing). The MemoryTools plugin registers training examples
    # for _memory_remember and _memory_recall intents at startup.
    #
    # Regex is used only for fact *extraction* — pulling the content from
    # "remember that **I prefer celsius**" once the classifier has already
    # identified the intent.

    # Patterns for extracting the fact content from a "remember" utterance
    _FACT_EXTRACTION_PATTERNS = [
        re.compile(r"^(?:please\s+)?(?:I\s+want\s+you\s+to\s+)?remember\s+(?:that\s+)?(.+)", re.IGNORECASE),
        re.compile(r"^(?:please\s+)?(?:don'?t\s+forget|note)\s+(?:that\s+)?(.+)", re.IGNORECASE),
        re.compile(r"^(?:please\s+)?keep\s+in\s+mind\s+(?:that\s+)?(.+)", re.IGNORECASE),
        re.compile(r"^(?:please\s+)?(?:save|store)\s+(?:that\s+)?(.+?)(?:\s+(?:to|in)\s+memory)?$", re.IGNORECASE),
        re.compile(r"^(?:please\s+)?make\s+a\s+note\s+(?:that\s+)?(.+)", re.IGNORECASE),
    ]

    def _detect_memory_intent(self, text: str) -> tuple[str | None, str | None]:
        """Detect memory intent using the IntentClassifier.

        Returns:
            (intent_type, content) where intent_type is "remember", "recall", or None.
            For "remember", content is the extracted fact.
            For "recall", content is the search query (the full user text).
        """
        from plugins.cores.memory_core import MEMORY_REMEMBER_INTENT, MEMORY_RECALL_INTENT, MEMORY_FORGET_ALL_INTENT

        try:
            classifier = self.plugin_system.get_intent_classifier()
            if not classifier or not classifier.model:
                return None, None

            predicted, confidence = classifier.predict_intent(text)
            logger.debug(f"[Memory] Intent classifier: {predicted} ({confidence:.2f})")

            if predicted == MEMORY_REMEMBER_INTENT and confidence >= 0.3:
                fact = self._extract_fact(text)
                if fact:
                    logger.info(f"[Memory] Detected REMEMBER intent (confidence={confidence:.2f}), fact: {fact}")
                    return "remember", fact
                else:
                    logger.debug(f"[Memory] REMEMBER intent detected but fact extraction failed for: {text}")

            elif predicted == MEMORY_RECALL_INTENT and confidence >= 0.3:
                logger.info(f"[Memory] Detected RECALL intent (confidence={confidence:.2f}), query: {text}")
                return "recall", text

            elif predicted == MEMORY_FORGET_ALL_INTENT and confidence >= 0.3:
                logger.info(f"[Memory] Detected FORGET_ALL intent (confidence={confidence:.2f})")
                return "forget_all", text

        except Exception as e:
            logger.warning(f"[Memory] Intent detection failed: {e}")

        return None, None

    def _extract_fact(self, text: str) -> str | None:
        """Extract the fact content from a 'remember' utterance using regex.

        E.g. "remember that I prefer celsius" → "I prefer celsius"
        Falls back to the full text if no pattern matches.
        """
        for pattern in self._FACT_EXTRACTION_PATTERNS:
            m = pattern.match(text.strip())
            if m:
                fact = m.group(1).strip().rstrip(".")
                if len(fact) > 3:
                    return fact
        # Fallback: use the full text as the fact (the classifier was confident
        # this is a remember intent, so the whole utterance is worth storing)
        stripped = text.strip().rstrip(".")
        return stripped if len(stripped) > 3 else None

    @staticmethod
    def _format_memories(memories: list[dict], header: str = "Relevant memories from past conversations") -> str:
        """Format retrieved memories as a system message for the LLM."""
        lines = [f"[{header}]"]
        for m in memories:
            ts = m["metadata"].get("timestamp", 0)
            age_s = time.time() - ts
            mem_type = m["metadata"].get("memory_type", "exchange")
            if age_s < 3600:
                age = f"{int(age_s / 60)}m ago"
            elif age_s < 86400:
                age = f"{int(age_s / 3600)}h ago"
            else:
                age = f"{int(age_s / 86400)}d ago"
            prefix = "[fact] " if mem_type == "fact" else ""
            lines.append(f"- {prefix}{m['document']} ({age})")
        lines.append(f"[End of memories]")
        return "\n".join(lines)

    @staticmethod
    def _format_memories_for_speech(memory_context: str) -> str:
        """Convert memory context string into natural spoken text for NLP mode."""
        # memory_context is the formatted string from _format_memories or a notification
        if "No relevant memories found" in memory_context:
            return "I don't have any memories about that."
        # Extract the memory lines (skip header/footer brackets)
        lines = []
        for line in memory_context.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                # Strip metadata like "(2h ago)" at the end
                text = line[2:]
                paren_idx = text.rfind(" (")
                if paren_idx > 0:
                    text = text[:paren_idx]
                text = text.replace("[fact] ", "")
                lines.append(text)
        if lines:
            return "Here's what I remember. " + ". ".join(lines) + "."
        return "I found some memories but couldn't format them."

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

        # -- Pre-LLM memory processing ------------------------------------------
        memory_context = None
        intent_type = None
        if content and self._memory_store and not _recursive:
            self._last_user_message = str(content)
            text = str(content)

            # 1. Check for explicit memory intent (remember/recall) via IntentClassifier
            intent_type, intent_content = self._detect_memory_intent(text)

            if intent_type == "forget_all":
                count = self._memory_store.clear_all()
                memory_context = (
                    f"[Memory system notification]\n"
                    f"All {count} memories have been cleared from persistent storage.\n"
                    f"Confirm to the user that your memory has been wiped clean."
                )
                logger.info(f"[Memory] Cleared all {count} memories")

            elif intent_type == "remember" and intent_content:
                self._memory_store.store_fact(intent_content, session_id=self._session_id)
                memory_context = (
                    f"[Memory system notification]\n"
                    f"The following fact has been stored in persistent memory: \"{intent_content}\"\n"
                    f"Confirm to the user briefly that you will remember this."
                )
                logger.info(f"[Memory] Stored explicit fact pre-LLM: {intent_content}")

            elif intent_type == "recall":
                memories = self._memory_store.search(query=intent_content or text)
                if memories:
                    memory_context = self._format_memories(
                        memories,
                        header="Memory search results — use these to answer the user's question"
                    )
                    logger.info(f"[Memory] Recall search returned {len(memories)} results")
                else:
                    memory_context = (
                        "[Memory search results]\n"
                        "No relevant memories found for this query.\n"
                        "Tell the user you don't have any memories about that topic."
                    )
                    logger.info("[Memory] Recall search returned no results")

            # 2. Default: automatic retrieval for context
            else:
                try:
                    logger.info(f"[Memory] Auto-retrieving memories for: {text[:100]}")
                    memories = self._memory_store.retrieve(
                        query=text,
                        activity_filter=self.message_manager.current_context.name,
                    )
                    if memories:
                        memory_context = self._format_memories(memories)
                        logger.info(f"[Memory] Injecting {len(memories)} memories into context")
                    else:
                        logger.info("[Memory] No relevant memories found")
                except Exception as e:
                    logger.warning(f"[Memory] Retrieval failed: {e}")

        # -- NLP mode: bypass LLM entirely -----------------------------------------
        if self._nlp_dispatcher and content and not _recursive:
            # Memory intents were already handled above — speak confirmation directly
            if memory_context and intent_type == "forget_all":
                self.tts_queue.put("Done. All memories have been cleared.")
                self.tts_queue.put("<EOS>")
                return
            if memory_context and intent_type == "remember":
                self.tts_queue.put("Got it, I'll remember that.")
                self.tts_queue.put("<EOS>")
                return
            if memory_context and intent_type == "recall":
                self.tts_queue.put(self._format_memories_for_speech(memory_context))
                self.tts_queue.put("<EOS>")
                return
            # Dispatch to NLP handler (classifies, extracts params, calls tool, speaks)
            self._nlp_dispatcher.dispatch(str(content), self.message_manager.current_context)
            return

        relevant_tools = plugin_manager.get_available_tools(
            architecture=self.client_type,
            activity=self.message_manager.current_context
        )
        logger.debug(f"Proposed relevant_tools: {[t.get('function', {}).get('name', '?') if isinstance(t, dict) else str(t)[:40] for t in relevant_tools]}")

        model_to_use = self.model

        try:
            response = self.stream_handler.stream_response(relevant_tools or tools, model=model_to_use, query=content, memory_context=memory_context)
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
                # Store the assistant message with tool_calls array (required by strict templates like Mistral)
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
                    self.message_manager.add_message_to_current_context(
                        "assistant", None, tool_calls=assistant_tool_calls
                    )

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
                    self.message_manager.add_message_to_current_context(
                        "tool", str(tool_result), name=tc["name"], tool_call_id=tc["id"]
                    )

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
