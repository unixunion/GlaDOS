"""On-demand chapter extraction from Gutenberg ZIM book entries.

Reads a book's HTML entry from its ZIM, strips ``pg-header`` / ``pg-footer``,
splits on ``<h2>`` tags, and returns a list of
``{"title": str, "paragraphs": List[str]}`` chapter dicts.

An LRU cache keeps a small number of recently-opened books ready in
memory. libzim's :class:`Archive` is not documented as thread-safe so
reads are serialized behind a per-archive :class:`threading.Lock`.
"""
from __future__ import annotations

import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger


class BookLoader:
    """Resolves book IDs → chapter lists, via cached ZIM reads."""

    def __init__(self, zim_dir: str = "data"):
        self._zim_dir = Path(zim_dir)
        self._archives: Dict[str, Tuple[object, threading.Lock]] = {}
        self._arch_lock = threading.Lock()

    def _open_archive(self, zim_filename: str):
        """Open (and cache) a libzim Archive handle for a given ZIM filename."""
        with self._arch_lock:
            cached = self._archives.get(zim_filename)
            if cached is not None:
                return cached
            path = self._zim_dir / zim_filename
            if not path.exists():
                logger.warning(f"[BookLoader] ZIM file not found: {path}")
                return None
            try:
                from libzim.reader import Archive
                archive = Archive(str(path))
            except Exception as e:
                logger.error(f"[BookLoader] Failed to open ZIM {path}: {e}")
                return None
            entry = (archive, threading.Lock())
            self._archives[zim_filename] = entry
            logger.info(f"[BookLoader] Opened ZIM {zim_filename} ({archive.entry_count} entries)")
            return entry

    def load_chapters(self, book: dict) -> List[Dict[str, object]]:
        """Read a book's chapter list. Results are LRU-cached per book.

        ``book`` must be a catalog record containing ``zim_filename`` and
        ``zim_entry_path``. Returns a list of dicts shaped
        ``{"title": str, "paragraphs": [str, ...]}``.
        """
        book_id = book["book_id"]
        cached = self._load_cached(book_id, book["zim_filename"], book["zim_entry_path"])
        # Return a fresh list-of-dicts; the cache stores an immutable tuple
        # so callers can mutate their copy without poisoning the cache.
        return [dict(ch) for ch in cached]

    @lru_cache(maxsize=16)
    def _load_cached(self, book_id: str, zim_filename: str, entry_path: str) -> Tuple:
        """LRU-cached read. Returns a tuple[frozenset-like-dict, ...] (hashable)."""
        chapters = self._read_chapters(zim_filename, entry_path)
        # Store as tuple-of-tuples so lru_cache treats them as hashable values
        return tuple(
            (("title", ch["title"]), ("paragraphs", tuple(ch["paragraphs"])))
            for ch in chapters
        )

    def _read_chapters(self, zim_filename: str, entry_path: str) -> List[Dict[str, object]]:
        pair = self._open_archive(zim_filename)
        if pair is None:
            return []
        archive, lock = pair
        with lock:
            try:
                entry = archive.get_entry_by_path(entry_path)
                item = entry.get_item()
                raw = bytes(item.content).decode("utf-8", errors="ignore")
            except Exception as e:
                logger.error(f"[BookLoader] Failed to read {entry_path} from {zim_filename}: {e}")
                return []
        return _split_html_to_chapters(raw)


def _split_html_to_chapters(html: str) -> List[Dict[str, object]]:
    """Strip PG boilerplate and split the HTML body on ``<h2>`` tags.

    Each chapter is a dict ``{"title": str, "paragraphs": [str, ...]}``.
    If the document has no ``<h2>`` headings, the whole body becomes one
    unnamed chapter so the reader can still open it.
    """
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.warning(f"[BookLoader] BS4 parse failed: {e}")
        return []

    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag_id in ("pg-header", "pg-footer"):
        el = soup.find(id=tag_id)
        if el:
            el.decompose()

    body = soup.body or soup
    chapters: List[Dict[str, object]] = []
    current_title: str = ""
    current_paragraphs: List[str] = []

    def _flush():
        # Drop chapters that ended up with neither content nor a real heading
        # (Gutenberg book pages emit h2 stubs like "CONTENTS." with no body).
        if current_paragraphs:
            chapters.append({
                "title": current_title or "Opening",
                "paragraphs": list(current_paragraphs),
            })

    # Manual sequential walk over body's descendants: <h2> starts a new
    # chapter, <p> adds a paragraph, nested <div>/<section> are flattened,
    # list-like tags fold into a single paragraph.
    def _walk(node):
        nonlocal current_title, current_paragraphs
        for child in list(node.children):
            if getattr(child, "name", None) is None:
                continue
            name = child.name.lower()
            if name == "h2":
                _flush()
                current_title = child.get_text(" ", strip=True)
                current_paragraphs = []
            elif name == "p":
                text = child.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text)
                if text:
                    current_paragraphs.append(text)
            elif name in ("div", "section", "article", "main"):
                _walk(child)
            # Other tags (blockquote, figure, lists, etc.) — fold into a paragraph
            elif name in ("blockquote", "ul", "ol", "pre"):
                text = child.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text)
                if text:
                    current_paragraphs.append(text)

    _walk(body)
    _flush()

    # Fallback: no h2s at all — rebuild a single chapter from all <p> tags
    if not chapters:
        paragraphs = []
        for p in body.find_all("p"):
            text = p.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            if text:
                paragraphs.append(text)
        if paragraphs:
            chapters.append({"title": "Opening", "paragraphs": paragraphs})

    # Also, `_walk` may produce a trailing "Opening" chapter before the first
    # h2 that actually contains front-matter like "CONTENTS". That's fine —
    # it becomes the first visible chapter in the reader.

    return chapters
