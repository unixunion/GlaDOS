"""Ebook reader plugin — Pass 1.

Provides a browseable library of public-domain Gutenberg books
(ingested into Qdrant via ``tools/ingest_ebooks_qdrant.py``) and a
static reader view with chapter navigation. TTS playback is wired up
in Pass 2, but the alternate :class:`KokoroSpeechModule` is constructed
in ``__init__`` so configuration errors surface at boot.
"""
from __future__ import annotations

import copy
import json
import os
import queue
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventHook, EventMessage
from glados.system.tts_runtime import TTSRuntime

from plugins.ebook_reader.book_loader import BookLoader
from plugins.ebook_reader.catalog import Catalog
from plugins.ebook_reader.ebook_speech import EbookSpeechModule
from plugins.ebook_reader.playback_worker import BookPlaybackWorker
from plugins.ebook_reader.qdrant_search import EbookQdrantSearch


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EbookReaderPlugin(RunnableMCPPlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()

        # --- Data directories and persistence ---
        self._data_dir = os.environ.get(
            "EBOOK_READER_DATA_DIR",
            os.path.join("plugin_data", "ebook_reader"),
        )
        os.makedirs(self._data_dir, exist_ok=True)

        # Catalog always lives next to state.json inside the data dir so the
        # env-var test isolation pattern works the same way pantry does.
        catalog_path = Path(self._data_dir) / "catalog.json"
        self._catalog = Catalog.load(catalog_path)

        self._state: Dict[str, Any] = self._load_state()
        self._state_dirty = False
        self._last_state_save_ts = 0.0

        # --- Book content loader (ZIM-on-demand) ---
        self._book_loader = BookLoader(zim_dir=self.plugin_config.get("zim_dir", "data"))

        # --- Qdrant semantic search (lazy) ---
        try:
            from glados.config import GladosConfig
            cfg = GladosConfig.from_yaml("glados_config.yml")
            qdrant_url = getattr(cfg, "qdrant_url", "http://localhost:6333")
        except Exception:
            qdrant_url = "http://localhost:6333"
        self._qdrant = EbookQdrantSearch(qdrant_url=qdrant_url)

        # --- Alternate TTS voice core + playback worker ---
        self._book_tts_queue: queue.Queue = queue.Queue()
        self._worker = BookPlaybackWorker(self)
        self._book_speech = self._build_book_speech_module()
        if self._book_speech is not None:
            logger.info(
                f"[EbookReader] Alternate voice core ready "
                f"(voice={self.plugin_config.get('book_voice', 'bf_isabella')})"
            )

        # --- System prompt ---
        self.register_system_prompt(
            "EBOOK READER: A library of public-domain books (Project Gutenberg) "
            "is available. Users can browse, search by genre or theme, and have "
            "books read aloud in a separate narrator voice."
        )

        # --- Tool registration (NLP intents wired in Pass 3) ---
        self.register_tool(
            handler=self.open_ebook,
            description=(
                "Open a book in the ebook reader by title or book ID. "
                "Use this when the user asks to start, open, or read a specific book."
            ),
            parameters={
                "title_or_id": {
                    "type": "string",
                    "description": "Book title (partial match OK) or a book_id like 'gutenberg-40161'",
                },
            },
            required=["title_or_id"],
            intents=[],
            activity=[Activity.GENERAL, Activity.ENTERTAINMENT],
            process_output=True,
        )
        self.register_tool(
            handler=self.play_book,
            description="Resume or start TTS playback of the currently selected book.",
            parameters={},
            required=[],
            intents=[],
            activity=[Activity.ENTERTAINMENT],
            process_output=True,
        )
        self.register_tool(
            handler=self.pause_book,
            description="Pause the currently playing book.",
            parameters={},
            required=[],
            intents=[],
            activity=[Activity.ENTERTAINMENT],
            process_output=True,
        )
        self.register_tool(
            handler=self.bookmark_here,
            description="Save a bookmark at the current reading position.",
            parameters={
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label for the bookmark",
                },
            },
            required=[],
            intents=[],
            activity=[Activity.ENTERTAINMENT],
            process_output=True,
        )

        # --- UI action handlers ---
        self.register_ui_action("ebook_library_action", self._on_library_action)
        self.register_ui_action("ebook_reader_action", self._on_reader_action)

        # --- Display views ---
        self.register_view(
            view_type="ebook_library",
            js_path="plugins/ebook_reader/views/library.js",
            css_path="plugins/ebook_reader/views/library.css",
            dashboard_card=True,
        )
        self.register_view(
            view_type="ebook_reader",
            js_path="plugins/ebook_reader/views/reader.js",
            css_path="plugins/ebook_reader/views/reader.css",
            dashboard_card=False,
        )

        logger.success(f"[EbookReader] Initialized — {len(self._catalog)} books in catalog")

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _state_path(self) -> str:
        return os.path.join(self._data_dir, "state.json")

    def _default_state(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "current_book_id": None,
            "position": {"chapter": 0, "paragraph": 0},
            "bookmarks": [],
            "recently_opened": [],
        }

    def _load_state(self) -> Dict[str, Any]:
        path = self._state_path()
        if not os.path.exists(path):
            return self._default_state()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._default_state()
            # Merge in any new keys we added since the file was written
            base = self._default_state()
            base.update(data)
            base["position"] = {**base.get("position", {}), **data.get("position", {})}
            return base
        except Exception as e:
            logger.warning(f"[EbookReader] Could not load state.json: {e}")
            return self._default_state()

    def _save_state(self) -> None:
        path = self._state_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            self._state_dirty = False
        except Exception as e:
            logger.warning(f"[EbookReader] Failed to write state.json: {e}")

    # ------------------------------------------------------------------
    # Alternate voice core construction
    # ------------------------------------------------------------------

    def _build_book_speech_module(self):
        """Build (but do not start) the alternate book-narration speech module.

        Constructs an :class:`EbookSpeechModule` (a Kokoro variant that
        suppresses listen-for-response on EOS and emits per-paragraph
        completion callbacks). The Kokoro model directory comes from
        ``book_voice_model`` in the plugin config — independent of the
        main assistant's ``voice_model``, so GlaDOS can keep her Piper
        voice while books read in Kokoro.
        """
        try:
            from glados.config import GladosConfig
            from kokoro_onnx import Kokoro
        except Exception as e:
            logger.warning(f"[EbookReader] Kokoro voice core not available: {e}")
            return None

        try:
            cfg = GladosConfig.from_yaml("glados_config.yml")
        except Exception as e:
            logger.warning(f"[EbookReader] Could not load GladosConfig for book voice: {e}")
            return None

        # Speaker selection — TTSConfig's speaker_id is what KokoroSpeechModule reads.
        book_voice = self.plugin_config.get("book_voice", "bf_isabella")
        cfg_for_book = copy.deepcopy(cfg)
        try:
            cfg_for_book.tts.speaker_id = book_voice
        except Exception as e:
            logger.warning(f"[EbookReader] Could not set speaker_id override: {e}")

        # Kokoro model path: independent from the main voice_model so the
        # main assistant can stay on Piper. Default to the standard layout.
        book_voice_model = self.plugin_config.get("book_voice_model", "kokoro-82m-onnx")
        model_dir = Path("models") / book_voice_model
        try:
            if model_dir.is_dir():
                model_file = model_dir / "kokoro-v0_19.onnx"
                voices_path = model_dir / "voices-v1.0.bin"
            else:
                model_file = model_dir
                voices_path = model_dir.parent / "voices-v1.0.bin"
            if not model_file.exists() or not voices_path.exists():
                logger.warning(
                    f"[EbookReader] Kokoro model files not found "
                    f"(model={model_file}, voices={voices_path}) — "
                    f"playback will be unavailable but plugin stays up. "
                    f"Set book_voice_model in glados_config.yml."
                )
                return None
            kokoro = Kokoro(str(model_file), str(voices_path))
        except Exception as e:
            logger.warning(f"[EbookReader] Failed to load Kokoro model: {e}")
            return None

        try:
            return EbookSpeechModule(
                tts=kokoro,
                tts_queue=self._book_tts_queue,
                speaking_lock=TTSRuntime().get_speaking_lock(),
                config=cfg_for_book,
                on_item_done=self._worker.on_item_done,
            )
        except Exception as e:
            logger.warning(f"[EbookReader] Failed to construct EbookSpeechModule: {e}")
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self.event_system.subscribe(
            "ui.ebook_library_action",
            EventHook("ebook_library_ui", callback=self._on_library_action, priority=5),
        )
        self.event_system.subscribe(
            "ui.ebook_reader_action",
            EventHook("ebook_reader_ui", callback=self._on_reader_action, priority=5),
        )
        self.event_system.subscribe(
            "system.tick",
            EventHook("ebook_state_save", callback=self._on_tick, priority=20),
        )
        # Wake-word interrupt → pause the book; flag for auto-resume
        self.event_system.subscribe(
            "system.interrupt_tts",
            EventHook("ebook_wake_pause", callback=self._on_wake_interrupt, priority=20),
        )
        # Main assistant finished speaking its response → auto-resume if we paused for it
        self.event_system.subscribe(
            "system.listen_for_response",
            EventHook("ebook_auto_resume", callback=self._on_listen_for_response, priority=20),
        )

        # Bring the speech module + worker online if Kokoro loaded successfully
        if self._book_speech is not None:
            try:
                self._book_speech.start()
            except Exception as e:
                logger.warning(f"[EbookReader] Failed to start book speech module: {e}")
                self._book_speech = None
        self._worker.start()

        # Push initial dashboard data so the home card has something to show
        self._publish_dashboard_data()
        logger.info("[EbookReader] Started — speech, worker, and subscriptions live")

    def stop(self):
        try:
            self._worker.stop()
        except Exception as e:
            logger.warning(f"[EbookReader] Worker stop error: {e}")
        if self._book_speech is not None:
            try:
                self._book_speech.stop()
            except Exception as e:
                logger.warning(f"[EbookReader] Speech module stop error: {e}")
        if self._state_dirty:
            self._save_state()
        logger.info("[EbookReader] Stopped")

    def _on_wake_interrupt(self, event: EventMessage) -> None:
        self._worker.on_wake_interrupt()

    def _on_listen_for_response(self, event: EventMessage) -> None:
        # The main assistant just finished speaking — resume the book if we paused for it
        self._worker.maybe_auto_resume()

    def _on_tick(self, event: EventMessage):
        # Throttled state save: at most once every 5s while dirty
        import time
        now = time.time()
        if self._state_dirty and (now - self._last_state_save_ts) >= 5.0:
            self._save_state()
            self._last_state_save_ts = now

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def open_ebook(self, title_or_id: str) -> dict:
        """Open a book by title or ID. Returns status + matched metadata."""
        query = (title_or_id or "").strip()
        if not query:
            return {"status": "error", "message": "No title or book ID provided."}

        book = self._catalog.get(query)
        if book is None:
            # Fuzzy title/author fallback: case-insensitive substring
            q_lower = query.lower()
            for b in self._catalog:
                title = (b.get("title") or "").lower()
                author = (b.get("author") or "").lower()
                if q_lower in title or q_lower in author:
                    book = b
                    break

        if book is None:
            return {
                "status": "not_found",
                "message": f"No book matching '{query}' in the library.",
            }

        self._set_current_book(book["book_id"])
        self._publish_reader_view()
        return {
            "status": "opened",
            "book_id": book["book_id"],
            "title": book["title"],
            "author": book["author"],
            "message": f"Opened {book['title']} by {book['author']}.",
        }

    def play_book(self) -> dict:
        if self._book_speech is None:
            return {
                "status": "error",
                "message": "Book narrator voice is not loaded. Check book_voice_model in config.",
            }
        if not self._state.get("current_book_id"):
            return {"status": "error", "message": "No book is open. Open one first."}
        self._worker.play()
        self._publish_reader_view()
        self._publish_dashboard_data()
        return {"status": "playing", "message": "Resumed book playback."}

    def pause_book(self) -> dict:
        self._worker.pause()
        self._publish_reader_view()
        self._publish_dashboard_data()
        return {"status": "paused", "message": "Book paused."}

    def bookmark_here(self, label: Optional[str] = None) -> dict:
        if not self._state.get("current_book_id"):
            return {"status": "error", "message": "No book is currently open."}
        entry = {
            "id": str(uuid.uuid4()),
            "book_id": self._state["current_book_id"],
            "chapter": self._state["position"].get("chapter", 0),
            "paragraph": self._state["position"].get("paragraph", 0),
            "label": label or "",
            "created_at": _now_iso(),
        }
        self._state.setdefault("bookmarks", []).append(entry)
        self._state_dirty = True
        self._save_state()
        return {
            "status": "saved",
            "bookmark_id": entry["id"],
            "message": "Bookmark saved.",
        }

    # ------------------------------------------------------------------
    # UI action handlers
    # ------------------------------------------------------------------

    def _on_library_action(self, event: EventMessage):
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action", "")
        if action == "show":
            self._publish_library_view()
        elif action == "get_state":
            # Dashboard card asking for a refresh — push data, don't navigate
            self._publish_dashboard_data()
        elif action == "open":
            book_id = data.get("book_id")
            if book_id:
                self._set_current_book(book_id)
                self._publish_reader_view()
        elif action == "search_semantic":
            query = (data.get("query") or "").strip()
            if not query:
                self._publish_library_view()
                return
            ids = self._qdrant.search(query, limit=24)
            self._publish_library_view(filtered_ids=ids, search_query=query)
        elif action == "clear_search":
            self._publish_library_view()

    def _on_reader_action(self, event: EventMessage):
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action", "")
        if action == "show":
            self._publish_reader_view()
        elif action == "next_chapter":
            self._advance_chapter(+1)
            self._publish_reader_view()
        elif action == "prev_chapter":
            self._advance_chapter(-1)
            self._publish_reader_view()
        elif action == "goto_chapter":
            try:
                idx = int(data.get("index", 0))
            except (TypeError, ValueError):
                idx = 0
            self._goto_chapter(idx)
            self._publish_reader_view()
        elif action == "bookmark":
            self.bookmark_here(label=data.get("label"))
            self._publish_reader_view()
        elif action == "back_to_library":
            self._publish_library_view()
        elif action == "play":
            self.play_book()
        elif action == "pause":
            self.pause_book()

    # ------------------------------------------------------------------
    # Navigation + state helpers
    # ------------------------------------------------------------------

    def _set_current_book(self, book_id: str) -> None:
        book = self._catalog.get(book_id)
        if book is None:
            logger.warning(f"[EbookReader] Unknown book_id {book_id}")
            return
        prev_id = self._state.get("current_book_id")
        # If switching to a different book, halt any in-progress playback
        # (don't lose the previous book's position — _state has been saved
        # progressively as paragraphs were spoken).
        if prev_id != book_id:
            self._worker.stop_for_book_change()
            self._state["position"] = {"chapter": 0, "paragraph": 0}
        elif not isinstance(self._state.get("position"), dict):
            self._state["position"] = {"chapter": 0, "paragraph": 0}
        self._state["current_book_id"] = book_id
        # Update recently_opened (move-to-front, cap at 10)
        recent = [r for r in self._state.get("recently_opened", []) if r.get("book_id") != book_id]
        recent.insert(0, {"book_id": book_id, "opened_at": _now_iso()})
        self._state["recently_opened"] = recent[:10]
        self._state_dirty = True
        self._save_state()
        self._publish_dashboard_data()

    def _current_chapters(self) -> List[Dict[str, Any]]:
        book_id = self._state.get("current_book_id")
        if not book_id:
            return []
        book = self._catalog.get(book_id)
        if not book:
            return []
        return self._book_loader.load_chapters(book)

    def _advance_chapter(self, delta: int) -> None:
        chapters = self._current_chapters()
        if not chapters:
            return
        cur = int(self._state["position"].get("chapter", 0))
        new = max(0, min(len(chapters) - 1, cur + delta))
        if new != cur:
            self._state["position"] = {"chapter": new, "paragraph": 0}
            self._state_dirty = True
            self._save_state()

    def _goto_chapter(self, index: int) -> None:
        chapters = self._current_chapters()
        if not chapters:
            return
        index = max(0, min(len(chapters) - 1, index))
        self._state["position"] = {"chapter": index, "paragraph": 0}
        self._state_dirty = True
        self._save_state()

    # ------------------------------------------------------------------
    # Display publishers
    # ------------------------------------------------------------------

    def _publish_dashboard_data(self) -> None:
        """Push a small summary blob the dashboard card reads from."""
        current_book_id = self._state.get("current_book_id")
        current_book = None
        if current_book_id:
            book = self._catalog.get(current_book_id)
            if book:
                current_book = {
                    "book_id": current_book_id,
                    "title": book.get("title", ""),
                    "author": book.get("author", ""),
                    "chapter_index": int(self._state["position"].get("chapter", 0)),
                    "chapter_count": int(book.get("chapter_count", 0)),
                }
        payload = {
            "total_books": len(self._catalog),
            "current_book": current_book,
            "is_playing": self._worker.is_playing(),
            "recently_opened_count": len(self._state.get("recently_opened", [])),
            "bookmark_count": len(self._state.get("bookmarks", [])),
        }
        self.event_system.publish(EventMessage(
            role="display", name="dashboard_data",
            content={"ebook_reader": payload},
            process_output=False,
        ))

    def _publish_library_view(self, filtered_ids: Optional[List[str]] = None,
                               search_query: Optional[str] = None) -> None:
        if filtered_ids is not None:
            filtered_books = self._catalog.filter_ids(filtered_ids)
            cards = [self._trim_for_card(b) for b in filtered_books]
        else:
            cards = self._catalog.list_cards()

        content = {
            "books": cards,
            "total_books": len(self._catalog),
            "current_book_id": self._state.get("current_book_id"),
            "recently_opened": self._state.get("recently_opened", [])[:8],
            "search_query": search_query,
            "is_filtered": filtered_ids is not None,
        }
        self.event_system.publish(EventMessage(
            role="display", name="ebook_library", content=content, process_output=False,
        ))

    @staticmethod
    def _trim_for_card(b: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "book_id": b["book_id"],
            "title": b.get("title", ""),
            "author": b.get("author", ""),
            "subjects": b.get("subjects", []),
            "lcc": b.get("lcc", ""),
            "language": b.get("language", "en"),
            "word_count": b.get("word_count", 0),
            "chapter_count": b.get("chapter_count", 0),
            "has_cover": bool(b.get("has_cover")),
        }

    def _publish_reader_view(self) -> None:
        book_id = self._state.get("current_book_id")
        if not book_id:
            # Nothing to show; bounce back to the library
            self._publish_library_view()
            return
        book = self._catalog.get(book_id)
        if not book:
            self._publish_library_view()
            return

        chapters = self._current_chapters()
        chapter_idx = int(self._state["position"].get("chapter", 0))
        chapter_idx = max(0, min(len(chapters) - 1, chapter_idx)) if chapters else 0
        chapter = chapters[chapter_idx] if chapters else {"title": "(empty)", "paragraphs": []}

        bookmarks = [
            b for b in self._state.get("bookmarks", [])
            if b.get("book_id") == book_id
        ]

        content = {
            "book": {
                "book_id": book_id,
                "title": book.get("title", ""),
                "author": book.get("author", ""),
                "subjects": book.get("subjects", []),
                "word_count": book.get("word_count", 0),
            },
            "chapter_index": chapter_idx,
            "chapter_count": len(chapters),
            "chapter": {
                "title": chapter.get("title", ""),
                "paragraphs": list(chapter.get("paragraphs", [])),
            },
            "is_playing": self._worker.is_playing(),
            "paragraph_index": int(self._state["position"].get("paragraph", 0)),
            "bookmarks": bookmarks,
        }
        self.event_system.publish(EventMessage(
            role="display", name="ebook_reader", content=content, process_output=False,
        ))
