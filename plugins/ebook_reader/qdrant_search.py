"""Lazy Qdrant client for semantic ebook search.

Deferred initialization so plugin boot doesn't fail when Qdrant is down.
Mirrors the connection pattern from ``plugins/recipes/recipe_qdrant.py``.
"""
from __future__ import annotations

import threading
from typing import List, Optional

from loguru import logger

COLLECTION_NAME = "ebooks"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"


class EbookQdrantSearch:
    """Thread-safe lazy Qdrant+embedder holder for ebook semantic search."""

    def __init__(self, qdrant_url: str = "http://localhost:6333"):
        self._qdrant_url = qdrant_url
        self._qdrant = None
        self._embed_model = None
        self._ready = False
        self._lock = threading.Lock()

    def _ensure_ready(self) -> bool:
        if self._ready:
            return True
        with self._lock:
            if self._ready:
                return True
            try:
                from qdrant_client import QdrantClient
                self._qdrant = QdrantClient(url=self._qdrant_url, timeout=5)
                self._qdrant.get_collections()
            except Exception as e:
                logger.warning(f"[EbookQdrant] Cannot connect to Qdrant at {self._qdrant_url}: {e}")
                return False
            try:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(EMBED_MODEL_NAME)
            except Exception as e:
                logger.warning(f"[EbookQdrant] Cannot load embedding model: {e}")
                return False
            # Verify the collection exists
            try:
                self._qdrant.get_collection(COLLECTION_NAME)
            except Exception:
                logger.warning(
                    f"[EbookQdrant] Collection '{COLLECTION_NAME}' not found. "
                    f"Run tools/ingest_ebooks_qdrant.py to populate it."
                )
                return False
            self._ready = True
            logger.info(f"[EbookQdrant] Ready — collection '{COLLECTION_NAME}' at {self._qdrant_url}")
            return True

    def search(self, query: str, limit: int = 24,
               score_threshold: float = 0.0) -> List[str]:
        """Return a ranked list of ``book_id`` strings for a natural-language query.

        Returns an empty list if Qdrant/embedder is unavailable or the
        query is empty. The sentinel ``__meta__`` point is filtered out.
        """
        query = (query or "").strip()
        if not query:
            return []
        if not self._ensure_ready():
            return []

        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            vec = self._embed_model.encode(query).tolist()
            response = self._qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=vec,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=Filter(
                    must_not=[FieldCondition(key="is_meta", match=MatchValue(value=True))],
                ),
            )
            hits = response.points if hasattr(response, "points") else []
        except Exception as e:
            logger.warning(f"[EbookQdrant] Search failed: {e}")
            return []

        book_ids = []
        for h in hits:
            payload = h.payload or {}
            bid = payload.get("book_id")
            if bid:
                book_ids.append(bid)
        return book_ids
