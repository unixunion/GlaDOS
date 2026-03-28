"""Knowledge RAG — retrieves relevant passages from Qdrant to augment LLM context.

Connects to a Qdrant vector store populated by the ingestion script (tools/ingest_zim.py).
Registers a PRE_LLM chat hook that searches for relevant knowledge passages and injects
them into the LLM context alongside memory.
"""
import threading
import time

from loguru import logger

from glados.config import GladosConfig
from glados.context.activity import Activity
from glados.llm.chat_hooks import ChatPipelinePhase, ChatContext
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin

# Global lock for sentence-transformers encoding — prevents segfaults when
# ChromaDB's internal embedding and our RAG embedding run on different threads
_embed_lock = threading.Lock()


class KnowledgeRAG(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        config = GladosConfig.from_yaml("glados_config.yml")
        self.enabled = getattr(config, "knowledge_enabled", False)
        self.qdrant_url = getattr(config, "qdrant_url", "http://localhost:6333")
        self.collections = getattr(config, "knowledge_collections", None) or []
        self.top_k = getattr(config, "knowledge_top_k", 3)
        self.threshold = getattr(config, "knowledge_threshold", 0.5)
        self.embed_model_name = getattr(config, "knowledge_embed_model", "all-MiniLM-L6-v2")

        self._qdrant = None
        self._embed_model = None
        self._ready = False  # Set True once background init completes
        self._init_lock = threading.Lock()

        if self.enabled and self.collections:
            collections_str = ", ".join(self.collections)
            self.register_system_prompt(
                f"KNOWLEDGE BASE: You have access to a knowledge base containing information from: {collections_str}. "
                "When your response includes passages tagged with <knowledge>, these are retrieved facts from "
                "your knowledge base — treat them as reliable reference material.\n"
                "When answering questions using knowledge base passages:\n"
                "- Use the information naturally, as if you know it yourself\n"
                "- Mention the source briefly when relevant (e.g. 'According to Wikipedia...')\n"
                "- If the passage doesn't fully answer the question, say what you found and note the gap\n"
                "- If no knowledge passages are provided, answer from your own training or say you don't know\n"
                "- Do NOT say 'based on the provided context' or 'the passage states' — speak naturally"
            )

    def _init_clients(self) -> bool:
        """Initialize Qdrant client and embedding model. Thread-safe."""
        if self._ready:
            return True
        with self._init_lock:
            if self._ready:
                return True
            try:
                from qdrant_client import QdrantClient
                self._qdrant = QdrantClient(url=self.qdrant_url, timeout=5)
                self._qdrant.get_collections()
                logger.info(f"[KnowledgeRAG] Connected to Qdrant at {self.qdrant_url}")
            except Exception as e:
                logger.warning(f"[KnowledgeRAG] Cannot connect to Qdrant at {self.qdrant_url}: {e}")
                self._qdrant = None
                return False

            try:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(self.embed_model_name)
                logger.info(f"[KnowledgeRAG] Loaded embedding model: {self.embed_model_name}")
            except Exception as e:
                logger.warning(f"[KnowledgeRAG] Cannot load embedding model: {e}")
                self._qdrant = None
                return False

            self._ready = True
            return True

    def start(self):
        if not self.enabled:
            logger.info("[KnowledgeRAG] Disabled via config (knowledge_enabled: false)")
            return
        if not self.collections:
            logger.warning("[KnowledgeRAG] No collections configured (knowledge_collections)")
            return

        # Initialize Qdrant + embedding model in background so it doesn't
        # block startup or the first TTS response
        def _bg_init():
            logger.info("[KnowledgeRAG] Background init starting...")
            if self._init_clients():
                logger.success("[KnowledgeRAG] Background init complete — ready")
            else:
                logger.warning("[KnowledgeRAG] Background init failed — RAG will be unavailable")
        threading.Thread(target=_bg_init, daemon=True, name="rag-init").start()

        # Register PRE_LLM hook — runs after memory (10), before hybrid NLP
        self.register_chat_hook(
            phase=ChatPipelinePhase.PRE_LLM,
            callback=self._rag_hook,
            priority=15,
        )

        # Register a tool so the LLM can actively search the knowledge base
        # when the passive RAG context isn't enough detail
        collections_str = ", ".join(self.collections)
        self.register_tool(
            handler=self.lookup_knowledge,
            description=(
                f"Search the knowledge base ({collections_str}) for detailed information on a topic. "
                "Use this when the user asks for MORE DETAIL about something already mentioned, "
                "or when you need to look up factual information that was not provided in the "
                "<knowledge> context. Returns relevant passages from encyclopedias and reference material."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": "The search query — be specific. E.g. 'Nintendo DS technical specifications' or 'history of the Eiffel Tower'.",
                },
            },
            required=["query"],
            intents=[
                "look up more about",
                "tell me more about",
                "search the knowledge base",
                "what does the encyclopedia say about",
                "look that up",
                "find more information about",
                "get more details on",
            ],
            process_output=True,
            activity=[Activity.GENERAL],
        )

        logger.success(f"[KnowledgeRAG] Active — searching {self.collections}, top_k={self.top_k}, threshold={self.threshold}")


    def stop(self):
        if self._qdrant:
            self._qdrant.close()
            self._qdrant = None

    # ---------------------------------------------------------------------------
    # Tool: active knowledge lookup (LLM calls this explicitly)
    # ---------------------------------------------------------------------------

    def lookup_knowledge(self, query: str) -> dict:
        """Search the knowledge base for detailed information. Called by the LLM as a tool."""
        if not self._ready:
            # Active tool call — worth waiting briefly for init to finish
            if not self._init_clients():
                return {"status": "error", "message": "Knowledge base is not available."}

        logger.info(f"[KnowledgeRAG] Tool lookup: '{query}'")
        t_start = time.perf_counter()

        try:
            with _embed_lock:
                query_vector = self._embed_model.encode(query).tolist()
        except Exception as e:
            return {"status": "error", "message": f"Embedding failed: {e}"}

        all_results = []
        for collection in self.collections:
            try:
                response = self._qdrant.query_points(
                    collection_name=collection,
                    query=query_vector,
                    limit=self.top_k * 2,  # fetch more for active lookup
                    score_threshold=self.threshold * 0.8,  # slightly lower threshold for explicit searches
                )
                hits = response.points if hasattr(response, 'points') else []
                for hit in hits:
                    payload = hit.payload or {}
                    all_results.append({
                        "text": payload.get("text", ""),
                        "title": payload.get("article_title", "Unknown"),
                        "source": payload.get("source", collection),
                        "score": hit.score,
                    })
            except Exception as e:
                logger.warning(f"[KnowledgeRAG] Lookup failed for '{collection}': {e}")

        elapsed_ms = (time.perf_counter() - t_start) * 1000

        if not all_results:
            logger.info(f"[KnowledgeRAG] Tool lookup: no results ({elapsed_ms:.0f}ms)")
            return {"status": "no_results", "message": f"No knowledge base entries found for '{query}'."}

        all_results.sort(key=lambda r: -r["score"])
        top = all_results[:self.top_k * 2]

        logger.info(f"[KnowledgeRAG] Tool lookup: {len(top)} results (best: {top[0]['score']:.2f}, {elapsed_ms:.0f}ms)")
        for r in top:
            logger.info(f"[KnowledgeRAG]   → {r['source']}/{r['title']} ({r['score']:.2f})")

        # Format as readable text for the LLM
        passages = []
        for r in top:
            passages.append(f"[{r['source']} — {r['title']}]\n{r['text']}")

        return {
            "status": "success",
            "results": len(top),
            "passages": "\n\n".join(passages),
        }

    # ---------------------------------------------------------------------------
    # Hook: passive knowledge injection (automatic, every request)
    # ---------------------------------------------------------------------------

    def _rag_hook(self, ctx: ChatContext) -> None:
        """PRE_LLM hook: search Qdrant for relevant knowledge and inject into context."""
        if ctx.handled:
            return

        # Skip very short inputs (tool commands, greetings)
        if len(ctx.user_text.strip()) < 10:
            logger.info(f"[KnowledgeRAG] Skipping short input: '{ctx.user_text}'")
            return

        # Skip if background init hasn't finished yet (don't block the chat pipeline)
        if not self._ready:
            logger.debug("[KnowledgeRAG] Not ready yet (background init in progress), skipping")
            return

        logger.info(f"[KnowledgeRAG] Searching for: '{ctx.user_text[:80]}'")
        t_start = time.perf_counter()

        try:
            with _embed_lock:
                query_vector = self._embed_model.encode(ctx.user_text).tolist()
        except Exception as e:
            logger.warning(f"[KnowledgeRAG] Embedding failed: {e}")
            return

        all_results = []
        for collection in self.collections:
            try:
                response = self._qdrant.query_points(
                    collection_name=collection,
                    query=query_vector,
                    limit=self.top_k,
                    score_threshold=self.threshold,
                )
                hits = response.points if hasattr(response, 'points') else []
                logger.info(f"[KnowledgeRAG] Collection '{collection}': {len(hits)} hits")
                for hit in hits:
                    payload = hit.payload or {}
                    all_results.append({
                        "text": payload.get("text", ""),
                        "title": payload.get("article_title", "Unknown"),
                        "source": payload.get("source", collection),
                        "score": hit.score,
                    })
            except Exception as e:
                logger.warning(f"[KnowledgeRAG] Search failed for collection '{collection}': {e}")

        elapsed_ms = (time.perf_counter() - t_start) * 1000

        if not all_results:
            logger.info(f"[KnowledgeRAG] No results above threshold {self.threshold} ({elapsed_ms:.0f}ms)")
            from glados.system.event_system import EventSystem, EventMessage as EM
            EventSystem().publish(EM("chat", "knowledge_status", {
                "role": "knowledge_miss",
                "query": ctx.user_text[:80],
            }))
            return

        # Sort by score, take top_k across all collections
        all_results.sort(key=lambda r: -r["score"])
        top_results = all_results[:self.top_k]

        knowledge = self._format_results(top_results)
        logger.info(
            f"[KnowledgeRAG] Injecting {len(top_results)} passages "
            f"(best score: {top_results[0]['score']:.2f}, {elapsed_ms:.0f}ms)"
        )
        for r in top_results:
            logger.info(f"[KnowledgeRAG]   → {r['source']}/{r['title']} (score: {r['score']:.2f}) | {r['text'][:120]}...")

        # Emit to chat panel
        from glados.system.event_system import EventSystem, EventMessage as EM
        EventSystem().publish(EM("chat", "knowledge", {
            "role": "knowledge",
            "sources": [{"title": r["title"], "source": r["source"], "score": round(r["score"], 2)} for r in top_results],
            "content": "\n".join(f"[{r['title']}] {r['text'][:200]}" for r in top_results),
        }))

        # Append to context (don't overwrite memory)
        if ctx.memory_context:
            ctx.memory_context += "\n\n" + knowledge
        else:
            ctx.memory_context = knowledge

    # ---------------------------------------------------------------------------
    # Formatting
    # ---------------------------------------------------------------------------

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        """Format Qdrant results as tagged context for the LLM."""
        lines = ["<knowledge>"]
        for r in results:
            lines.append(f"<source name=\"{r['source']}\" article=\"{r['title']}\" relevance=\"{r['score']:.0%}\">")
            lines.append(r["text"])
            lines.append("</source>")
        lines.append("</knowledge>")
        return "\n".join(lines)
