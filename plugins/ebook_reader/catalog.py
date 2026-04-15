"""Ebook catalog loader + simple client-side search helpers.

The catalog is a flat JSON file produced by ``tools/ingest_ebooks_qdrant.py``.
It is loaded once at plugin startup and kept in memory. Qdrant is reserved
for semantic search; plain title/author substring filtering stays local.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class Catalog:
    """In-memory catalog of ebooks, keyed by ``book_id``."""

    def __init__(self, books: Optional[List[Dict[str, Any]]] = None, path: Optional[Path] = None):
        self._books: List[Dict[str, Any]] = list(books or [])
        self._by_id: Dict[str, Dict[str, Any]] = {b["book_id"]: b for b in self._books}
        self._path = path

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        if not path.exists():
            logger.warning(f"[EbookCatalog] No catalog at {path} — starting empty")
            return cls(books=[], path=path)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[EbookCatalog] Failed to read {path}: {e}")
            return cls(books=[], path=path)
        books = data.get("books", []) if isinstance(data, dict) else []
        logger.info(f"[EbookCatalog] Loaded {len(books)} books from {path}")
        return cls(books=books, path=path)

    def __len__(self) -> int:
        return len(self._books)

    def __iter__(self):
        return iter(self._books)

    def all(self) -> List[Dict[str, Any]]:
        return list(self._books)

    def get(self, book_id: str) -> Optional[Dict[str, Any]]:
        return self._by_id.get(book_id)

    def list_cards(self) -> List[Dict[str, Any]]:
        """Return a trimmed, UI-friendly view for the library grid.

        Drops fields the JS doesn't need (source_url, zim paths) to keep
        the over-the-wire payload small.
        """
        cards = []
        for b in self._books:
            cards.append({
                "book_id": b["book_id"],
                "title": b.get("title", ""),
                "author": b.get("author", ""),
                "subjects": b.get("subjects", []),
                "lcc": b.get("lcc", ""),
                "language": b.get("language", "en"),
                "word_count": b.get("word_count", 0),
                "chapter_count": b.get("chapter_count", 0),
                "has_cover": bool(b.get("has_cover")),
            })
        return cards

    def filter_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        id_set = set(ids)
        # Preserve the order passed in (semantic-search result ranking)
        by_order = {bid: i for i, bid in enumerate(ids)}
        found = [b for b in self._books if b["book_id"] in id_set]
        found.sort(key=lambda b: by_order.get(b["book_id"], 1 << 30))
        return found
