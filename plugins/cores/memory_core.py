"""Memory Core — registers memory intents and handles all memory operations via chat pipeline hook.

All memory logic (intent detection, fact extraction, retrieval, formatting) lives here.
ChatClient just runs the hook — no memory imports needed in the core LLM code.
"""
import re
import time

from loguru import logger

from glados.context.activity import Activity
from glados.llm.chat_hooks import ChatPipelinePhase, ChatContext
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage
from glados.system.intent_classifier import IntentClassifier


# Intent names — not real tools, handled entirely by this plugin's hook
MEMORY_REMEMBER_INTENT = "_memory_remember"
MEMORY_RECALL_INTENT = "_memory_recall"
MEMORY_FORGET_ALL_INTENT = "_memory_forget_all"
MEMORY_DEBUG_INTENT = "_memory_debug"

# Patterns for extracting the fact content from a "remember" utterance
_FACT_EXTRACTION_PATTERNS = [
    re.compile(r"^(?:please\s+)?(?:I\s+want\s+you\s+to\s+)?remember\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:don'?t\s+forget|note)\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?keep\s+in\s+mind\s+(?:that\s+)?(.+)", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?(?:save|store)\s+(?:that\s+)?(.+?)(?:\s+(?:to|in)\s+memory)?$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?make\s+a\s+note\s+(?:that\s+)?(.+)", re.IGNORECASE),
]


class MemoryCore(RunnableMCPPlugin):
    """Registers memory intents with the IntentClassifier, provides a system prompt,
    and handles all memory operations via a PRE_LLM chat pipeline hook.
    """

    def __init__(self):
        super().__init__()
        logger.info("Instantiating Memory Core")
        self._store = None  # Lazy — set in start() from ChatClient's store

        # Register memory intents with the shared IntentClassifier
        classifier = IntentClassifier()
        classifier.add_intent(MEMORY_REMEMBER_INTENT, [
            "remember that",
            "remember this",
            "don't forget",
            "don't forget that",
            "note that",
            "keep in mind",
            "keep in mind that",
            "save that",
            "save to memory",
            "store in memory",
            "please remember",
            "I want you to remember",
            "make a note",
            "remember my preference",
            "remember I like",
            "remember I prefer",
            "remember I am",
            "remember I have",
        ])
        # Note: recall intent removed — memory recall is handled by auto-retrieval
        # which injects relevant memories into LLM context. The LLM answers naturally.
        classifier.add_intent(MEMORY_FORGET_ALL_INTENT, [
            "forget everything",
            "clear your memory",
            "clear all memories",
            "erase your memory",
            "delete all memories",
            "wipe your memory",
            "reset your memory",
            "forget all",
            "clear memory",
            "erase all memories",
        ])
        classifier.add_intent(MEMORY_DEBUG_INTENT, [
            "dump memories",
            "debug memories",
            "dump memory",
            "debug memory",
            "memory dump",
            "memory debug",
            "log all memories",
            "print all memories",
        ])
        classifier.retrain()
        logger.success("Memory intents registered with IntentClassifier")

        # Register with PluginSystem so activity inference works for memory intents
        from glados.system.plugin import PluginSystem
        plugin_system = PluginSystem()
        for intent_name in [MEMORY_REMEMBER_INTENT, MEMORY_FORGET_ALL_INTENT, MEMORY_DEBUG_INTENT]:
            plugin_system.plugins[intent_name] = {
                "function": None,
                "description": "Memory operation (handled pre-LLM)",
                "llm_function_request": {},
                "process_output": True,
                "callable": None,
                "activity": [Activity.GENERAL, Activity.SYSTEM],
            }

        self.register_system_prompt(
            "MEMORY SYSTEM: You have your own persistent memory. Your memories are included in this conversation "
            "as system messages labeled [Relevant memories from past conversations] or [Memory search results]. "
            "These are YOUR memories — things YOU have learned from previous conversations with this user. "
            "You have full permission to read, reference, and summarize them.\n\n"
            "When you see memory entries, speak about them naturally as things you remember: "
            "'I remember you mentioned...' or 'From our previous conversation...'.\n"
            "When the user asks 'what do you remember' or 'list memories', read through the memory entries "
            "in your system messages and summarize them for the user.\n"
            "Do NOT say you cannot access memories — they are right here in your context.\n"
            "Entries marked [fact] are things the user explicitly asked you to remember — prioritize these."
        )

    def start(self):
        # Get the memory store from ChatClient (lazy import to avoid circular deps)
        try:
            from glados.llm.memory.store import VectorMemoryStore
            self._store = VectorMemoryStore()
        except Exception as e:
            logger.warning(f"[MemoryCore] Could not get memory store: {e}")

        # Register the PRE_LLM hook — this replaces all memory logic in chat_client.py
        self.register_chat_hook(
            phase=ChatPipelinePhase.PRE_LLM,
            callback=self._pre_llm_hook,
            priority=10,  # runs early, before hybrid NLP
        )

        logger.info("MemoryCore plugin started")
        logger.success("[MemoryCore] Online")

    def stop(self):
        logger.info("MemoryCore plugin stopped")

    # ---------------------------------------------------------------------------
    # Chat pipeline hook
    # ---------------------------------------------------------------------------

    def _pre_llm_hook(self, ctx: ChatContext) -> None:
        """Pre-LLM hook: detect memory intents, handle them, or inject auto-retrieved context."""
        if not self._store:
            return

        text = ctx.user_text
        session_id = ctx.session_id

        # 1. Check for explicit memory intent
        intent_type, intent_content = self._detect_memory_intent(text)

        if intent_type == "debug":
            count = self._store.dump_all()
            ctx.tts_queue.put(f"Dumped {count} memories to the log.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return

        if intent_type == "forget_all":
            count = self._store.clear_all()
            logger.info(f"[Memory] Cleared all {count} memories")
            ctx.tts_queue.put(f"Done. All {count} memories have been cleared.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return

        if intent_type == "remember" and intent_content:
            self._store.store_fact(intent_content, session_id=session_id)
            logger.info(f"[Memory] Stored fact: {intent_content}")
            ctx.tts_queue.put("Got it, I'll remember that.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return

        # Recall is handled by auto-retrieval below — the LLM sees injected
        # memories in its context and answers naturally. No NLP interception needed.

        # 2. Default: automatic retrieval for context enrichment
        try:
            logger.info(f"[Memory] Auto-retrieving memories for: {text[:100]}")
            memories = self._store.retrieve(
                query=text,
                activity_filter=ctx.activity.name,
            )
            if memories:
                ctx.memory_context = self._format_memories(memories)
                logger.info(f"[Memory] Injecting {len(memories)} memories into context")
            else:
                logger.info("[Memory] No relevant memories found")
        except Exception as e:
            logger.warning(f"[Memory] Retrieval failed: {e}")

    # ---------------------------------------------------------------------------
    # Intent detection
    # ---------------------------------------------------------------------------

    def _detect_memory_intent(self, text: str) -> tuple[str | None, str | None]:
        """Detect memory intent using the IntentClassifier.

        Returns (intent_type, content) — "remember"/"recall"/"forget_all"/"debug" or (None, None).
        """
        # Fast keyword check for debug (classifier often misroutes these)
        text_lower = text.strip().lower()
        if any(kw in text_lower for kw in ["dump memor", "debug memor", "memory dump", "memory debug", "log all memor", "print all memor"]):
            logger.info(f"[Memory] Detected DEBUG intent via keyword match")
            return "debug", text

        try:
            classifier = IntentClassifier()
            if not classifier.model:
                return None, None

            predicted, confidence = classifier.predict_intent(text)
            logger.debug(f"[Memory] Intent classifier: {predicted} ({confidence:.2f})")

            if predicted == MEMORY_REMEMBER_INTENT and confidence >= 0.3:
                fact = self._extract_fact(text)
                if fact:
                    logger.info(f"[Memory] Detected REMEMBER intent (confidence={confidence:.2f}), fact: {fact}")
                    return "remember", fact
                else:
                    logger.debug(f"[Memory] REMEMBER intent detected but extraction failed for: {text}")

            elif predicted == MEMORY_RECALL_INTENT and confidence >= 0.85:
                logger.info(f"[Memory] Detected RECALL intent (confidence={confidence:.2f})")
                return "recall", text

            elif predicted == MEMORY_FORGET_ALL_INTENT and confidence >= 0.3:
                logger.info(f"[Memory] Detected FORGET_ALL intent (confidence={confidence:.2f})")
                return "forget_all", text

            elif predicted == MEMORY_DEBUG_INTENT and confidence >= 0.3:
                logger.info(f"[Memory] Detected DEBUG intent (confidence={confidence:.2f})")
                return "debug", text

        except Exception as e:
            logger.warning(f"[Memory] Intent detection failed: {e}")

        return None, None

    # ---------------------------------------------------------------------------
    # Fact extraction
    # ---------------------------------------------------------------------------

    @staticmethod
    def _extract_fact(text: str) -> str | None:
        """Extract fact content from a 'remember' utterance using regex.
        E.g. "remember that I prefer celsius" → "I prefer celsius"
        """
        for pattern in _FACT_EXTRACTION_PATTERNS:
            m = pattern.match(text.strip())
            if m:
                fact = m.group(1).strip().rstrip(".")
                if len(fact) > 3:
                    return fact
        stripped = text.strip().rstrip(".")
        return stripped if len(stripped) > 3 else None

    # ---------------------------------------------------------------------------
    # Memory formatting
    # ---------------------------------------------------------------------------

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
        lines.append("[End of memories]")
        return "\n".join(lines)
