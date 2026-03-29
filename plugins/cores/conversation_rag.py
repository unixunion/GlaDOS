"""Conversation RAG — retrieves relevant prior exchanges to enrich LLM context.

Replaces the sliding window approach: instead of keeping 20 messages in history,
keeps a small immediate window (last few turns) and retrieves relevant prior
exchanges from Qdrant via semantic search.

PRE_LLM hook: searches for relevant prior conversations and injects them
POST_RESPONSE hook: stores the current exchange for future retrieval
"""
import threading
import time

from loguru import logger

from glados.config import GladosConfig
from glados.llm.chat_hooks import ChatPipelinePhase, ChatContext
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin


class ConversationRAG(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        config = GladosConfig.from_yaml("glados_config.yml")
        self.enabled = getattr(config, "conversation_rag_enabled", False)
        self.top_k = getattr(config, "conversation_rag_top_k", 5)
        self.threshold = getattr(config, "conversation_rag_threshold", 0.4)
        qdrant_url = getattr(config, "qdrant_url", "http://localhost:6333")
        embed_model = getattr(config, "knowledge_embed_model", "all-MiniLM-L6-v2")

        self._store = None
        self._ready = False
        self._qdrant_url = qdrant_url
        self._embed_model_name = embed_model

    def _init_store(self) -> bool:
        """Initialize the conversation store. Thread-safe."""
        if self._ready:
            return True
        try:
            from glados.llm.memory.conversation_store import ConversationStore
            self._store = ConversationStore(
                qdrant_url=self._qdrant_url,
                embed_model_name=self._embed_model_name,
            )
            # Force client init now (loads embedding model)
            count = self._store.get_count()
            self._ready = True
            logger.success(f"[ConversationRAG] Ready — {count} prior exchanges")
            return True
        except Exception as e:
            logger.warning(f"[ConversationRAG] Init failed: {e}")
            return False

    def start(self):
        if not self.enabled:
            logger.info("[ConversationRAG] Disabled via config")
            return

        # PRE_LLM: retrieve relevant prior exchanges (priority 12, after memory, before knowledge RAG)
        self.register_chat_hook(
            phase=ChatPipelinePhase.PRE_LLM,
            callback=self._pre_llm_hook,
            priority=12,
        )

        # POST_RESPONSE: store the exchange for future retrieval
        self.register_chat_hook(
            phase=ChatPipelinePhase.POST_RESPONSE,
            callback=self._post_response_hook,
            priority=50,
        )

        # Initialize in background so startup isn't blocked
        def _bg_init():
            logger.info("[ConversationRAG] Background init starting...")
            self._init_store()
        threading.Thread(target=_bg_init, daemon=True, name="conv-rag-init").start()

    def stop(self):
        pass

    # ---------------------------------------------------------------------------
    # PRE_LLM: retrieve relevant prior conversations
    # ---------------------------------------------------------------------------

    def _pre_llm_hook(self, ctx: ChatContext) -> None:
        """Search for relevant prior exchanges and inject into context."""
        if ctx.handled:
            return
        if not self._ready:
            return

        # Skip very short inputs
        if len(ctx.user_text.strip()) < 8:
            return

        t_start = time.perf_counter()
        results = self._store.retrieve_relevant(
            query=ctx.user_text,
            top_k=self.top_k,
            threshold=self.threshold,
        )
        elapsed_ms = (time.perf_counter() - t_start) * 1000

        if not results:
            logger.debug(f"[ConversationRAG] No relevant prior exchanges ({elapsed_ms:.0f}ms)")
            return

        context = self._format_exchanges(results)
        logger.info(
            f"[ConversationRAG] Injecting {len(results)} prior exchanges "
            f"(best: {results[0]['score']:.2f}, {elapsed_ms:.0f}ms)"
        )

        # Prepend to memory_context (before knowledge RAG)
        if ctx.memory_context:
            ctx.memory_context = context + "\n\n" + ctx.memory_context
        else:
            ctx.memory_context = context

    # ---------------------------------------------------------------------------
    # POST_RESPONSE: store the exchange
    # ---------------------------------------------------------------------------

    def _post_response_hook(self, ctx: ChatContext) -> None:
        """Store the user+assistant exchange for future retrieval."""
        if not ctx.user_text or not self._ready:
            return

        # Get the assistant's response from the extra dict or message manager
        # The response_processor stores the full response, but we need to get it
        # from the message manager's last assistant message
        try:
            from glados.llm.message_manager import MessageManager
            mm = MessageManager()
            messages = list(mm.get_messages())
            # Find the last assistant message
            assistant_msg = None
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                    assistant_msg = msg["content"]
                    break

            if not assistant_msg:
                logger.debug("[ConversationRAG] No assistant message found to store")
                return

            # Truncate very long responses (we want the summary, not the novel)
            if len(assistant_msg) > 500:
                assistant_msg = assistant_msg[:500]

            logger.info(f"[ConversationRAG] Storing exchange: user='{ctx.user_text[:60]}' assistant='{assistant_msg[:60]}'")
            self._store.store_exchange(
                user_msg=ctx.user_text,
                assistant_msg=assistant_msg,
                session_id=ctx.session_id,
                activity=ctx.activity.name,
            )
        except Exception as e:
            logger.debug(f"[ConversationRAG] Failed to store exchange: {e}")

    # ---------------------------------------------------------------------------
    # Formatting
    # ---------------------------------------------------------------------------

    @staticmethod
    def _format_exchanges(results: list[dict]) -> str:
        """Format retrieved exchanges as a context block."""
        lines = ["[Prior conversation context — relevant past exchanges]"]
        for r in results:
            ts = r.get("timestamp", 0)
            age_s = time.time() - ts
            if age_s < 3600:
                age = f"{int(age_s / 60)}m ago"
            elif age_s < 86400:
                age = f"{int(age_s / 3600)}h ago"
            else:
                age = f"{int(age_s / 86400)}d ago"

            # Use the stored document (User: ...\nAssistant: ...)
            lines.append(f"- ({age}) {r['document']}")
        lines.append("[End of prior context]")
        return "\n".join(lines)
