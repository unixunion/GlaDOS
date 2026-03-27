"""Qdrant-backed conversation exchange store for conversation RAG.

Stores user+assistant exchanges as vector embeddings, enabling semantic
retrieval of relevant prior conversations to inject into LLM context.
"""
import threading
import time
import uuid

from loguru import logger


# Shared lock with knowledge RAG to prevent concurrent sentence-transformer calls
_embed_lock = threading.Lock()


class ConversationStore:
    """Stores and retrieves conversation exchanges in Qdrant."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, qdrant_url: str = "http://localhost:6333",
                 embed_model_name: str = "all-MiniLM-L6-v2",
                 collection_name: str = "conversations"):
        if self._initialized:
            return
        self._qdrant_url = qdrant_url
        self._embed_model_name = embed_model_name
        self._collection_name = collection_name
        self._qdrant = None
        self._embed_model = None
        self._initialized = True

    def _ensure_clients(self) -> bool:
        """Lazy init of Qdrant client and embedding model."""
        if self._qdrant is not None and self._embed_model is not None:
            return True
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            self._qdrant = QdrantClient(url=self._qdrant_url, timeout=5)

            # Create collection if it doesn't exist
            collections = [c.name for c in self._qdrant.get_collections().collections]
            if self._collection_name not in collections:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(self._embed_model_name)
                dim = model.get_sentence_embedding_dimension()
                self._qdrant.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                self._embed_model = model
                logger.info(f"[ConversationStore] Created collection '{self._collection_name}' (dim={dim})")
            else:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(self._embed_model_name)

            info = self._qdrant.get_collection(self._collection_name)
            logger.info(f"[ConversationStore] Connected — {info.points_count} exchanges stored")
            return True
        except Exception as e:
            logger.warning(f"[ConversationStore] Init failed: {e}")
            self._qdrant = None
            self._embed_model = None
            return False

    def store_exchange(self, user_msg: str, assistant_msg: str,
                       session_id: str, activity: str) -> bool:
        """Store a user+assistant exchange pair."""
        if not self._ensure_clients():
            return False

        document = f"User: {user_msg}\nAssistant: {assistant_msg}"

        try:
            from qdrant_client.models import PointStruct
            with _embed_lock:
                vector = self._embed_model.encode(document).tolist()

            point = PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "document": document,
                    "user_message": user_msg,
                    "assistant_message": assistant_msg,
                    "session_id": session_id,
                    "activity": activity,
                    "timestamp": time.time(),
                },
            )
            self._qdrant.upsert(
                collection_name=self._collection_name,
                points=[point],
            )
            logger.debug(f"[ConversationStore] Stored exchange: {user_msg[:60]}...")
            return True
        except Exception as e:
            logger.warning(f"[ConversationStore] Store failed: {e}")
            return False

    def retrieve_relevant(self, query: str, top_k: int = 5,
                          threshold: float = 0.4) -> list[dict]:
        """Retrieve exchanges most relevant to the query."""
        if not self._ensure_clients():
            return []

        try:
            with _embed_lock:
                query_vector = self._embed_model.encode(query).tolist()

            response = self._qdrant.query_points(
                collection_name=self._collection_name,
                query=query_vector,
                limit=top_k,
                score_threshold=threshold,
            )
            hits = response.points if hasattr(response, 'points') else []

            results = []
            for hit in hits:
                payload = hit.payload or {}
                results.append({
                    "document": payload.get("document", ""),
                    "user_message": payload.get("user_message", ""),
                    "assistant_message": payload.get("assistant_message", ""),
                    "session_id": payload.get("session_id", ""),
                    "activity": payload.get("activity", ""),
                    "timestamp": payload.get("timestamp", 0),
                    "score": hit.score,
                })
            return results
        except Exception as e:
            logger.warning(f"[ConversationStore] Retrieval failed: {e}")
            return []

    def get_count(self) -> int:
        """Get the number of stored exchanges."""
        if not self._ensure_clients():
            return 0
        try:
            info = self._qdrant.get_collection(self._collection_name)
            return info.points_count
        except Exception:
            return 0

    def clear_all(self) -> int:
        """Delete all exchanges. Returns count deleted."""
        if not self._ensure_clients():
            return 0
        try:
            count = self.get_count()
            from qdrant_client.models import Distance, VectorParams
            dim = self._embed_model.get_sentence_embedding_dimension()
            self._qdrant.delete_collection(self._collection_name)
            self._qdrant.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info(f"[ConversationStore] Cleared {count} exchanges")
            return count
        except Exception as e:
            logger.warning(f"[ConversationStore] Clear failed: {e}")
            return 0
