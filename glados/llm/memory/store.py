import time
from typing import Optional

import chromadb
from loguru import logger


class VectorMemoryStore:
    """ChromaDB-backed persistent vector memory store for GlaDOS.

    Stores two kinds of documents:
    - Exchange pairs (user+assistant) — stored automatically after each LLM response
    - Explicit facts — stored when the user says "remember that..."

    Retrieval always merges activity-filtered exchanges with explicit memories,
    so facts like "I prefer celsius" are recalled regardless of activity context.
    """

    _instance: Optional["VectorMemoryStore"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: str = "data/memory_db", top_k: int = 5):
        if self._initialized:
            return
        self._db_path = db_path
        self._top_k = top_k
        self._client = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_or_create_collection(
            name="glados_memory",
            metadata={"hnsw:space": "cosine"},
        )
        self._initialized = True
        logger.success(f"VectorMemoryStore initialized at {db_path} ({self._collection.count()} existing documents)")

    def store_exchange(self, user_message: str, assistant_message: str, activity: str, session_id: str) -> None:
        """Store a user+assistant exchange pair (automatic, every turn)."""
        document = f"User: {user_message}\nAssistant: {assistant_message}"
        doc_id = f"{session_id}:{time.time_ns()}"
        metadata = {
            "activity": activity,
            "memory_type": "exchange",
            "session_id": session_id,
            "timestamp": time.time(),
        }
        try:
            self._collection.add(
                documents=[document],
                ids=[doc_id],
                metadatas=[metadata],
            )
            logger.info(f"[MemoryStore] Stored exchange id={doc_id}, activity={activity}, total={self._collection.count()}")
            logger.debug(f"[MemoryStore] Document: {document[:120]}")
        except Exception as e:
            logger.error(f"[MemoryStore] Failed to store exchange: {e}")

    def store_fact(self, fact: str, session_id: str) -> None:
        """Store an explicit fact (user said 'remember that...')."""
        doc_id = f"fact:{time.time_ns()}"
        metadata = {
            "activity": "EXPLICIT",
            "memory_type": "fact",
            "session_id": session_id,
            "timestamp": time.time(),
        }
        try:
            self._collection.add(
                documents=[fact],
                ids=[doc_id],
                metadatas=[metadata],
            )
            logger.success(f"[MemoryStore] Stored fact: {fact[:80]}, total={self._collection.count()}")
        except Exception as e:
            logger.error(f"[MemoryStore] Failed to store fact: {e}")

    def retrieve(self, query: str, top_k: int = None, activity_filter: str = None) -> list[dict]:
        """Retrieve relevant memories. Merges activity-filtered results with explicit facts.

        This ensures that explicit facts (e.g. "prefers celsius") are always
        retrievable regardless of the current activity context.
        """
        n = top_k or self._top_k
        count = self._collection.count()
        logger.info(f"[MemoryStore] Querying {count} documents, top_k={n}, activity_filter={activity_filter}")
        if count == 0:
            logger.info("[MemoryStore] Collection empty, skipping query")
            return []

        all_results = {}  # doc_id -> {document, metadata, distance}

        # Query 1: activity-filtered (conversation exchanges in this context)
        if activity_filter:
            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=min(n, count),
                    where={"activity": activity_filter},
                )
                self._merge_results(results, all_results)
            except Exception as e:
                logger.warning(f"[MemoryStore] Activity-filtered query failed: {e}")

        # Query 2: explicit facts (always included regardless of activity)
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(n, count),
                where={"memory_type": "fact"},
            )
            self._merge_results(results, all_results)
        except Exception as e:
            logger.warning(f"[MemoryStore] Fact query failed: {e}")

        # If no activity filter, do a broad search
        if not activity_filter:
            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=min(n, count),
                )
                self._merge_results(results, all_results)
            except Exception as e:
                logger.warning(f"[MemoryStore] Broad query failed: {e}")

        # Filter by distance, sort by relevance, take top_k
        memories = [
            v for v in all_results.values()
            if v["distance"] <= 1.5
        ]
        memories.sort(key=lambda m: m["distance"])
        memories = memories[:n]

        logger.info(f"[MemoryStore] Returning {len(memories)} memories (from {len(all_results)} candidates)")
        for i, m in enumerate(memories):
            logger.debug(f"[MemoryStore]   #{i+1} dist={m['distance']:.3f} type={m['metadata'].get('memory_type', '?')}: {m['document'][:60]}")

        return memories

    def search(self, query: str, top_k: int = None) -> list[dict]:
        """Broad unfiltered search across all memories. Used for explicit recall requests."""
        n = top_k or self._top_k
        count = self._collection.count()
        if count == 0:
            return []

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=min(n * 2, count),  # cast a wider net for recall
            )
        except Exception as e:
            logger.warning(f"[MemoryStore] Search query failed: {e}")
            return []

        memories = []
        if results and results["documents"]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                if dist > 1.5:
                    continue
                memories.append({
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })

        logger.info(f"[MemoryStore] Broad search returned {len(memories)} results for: {query[:60]}")
        return memories

    @staticmethod
    def _merge_results(results, target: dict) -> None:
        """Merge ChromaDB query results into target dict, keeping lowest distance per document."""
        if not results or not results["documents"]:
            return
        for doc_id, doc, meta, dist in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if doc_id not in target or dist < target[doc_id]["distance"]:
                target[doc_id] = {
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                }
