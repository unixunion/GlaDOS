"""Background TTS playback worker for the ebook reader.

Walks the current book's chapter paragraphs, enqueues them one-by-one to
the book's :class:`EbookSpeechModule`, and tracks position in the plugin's
state so that:

- Pausing mid-paragraph does NOT advance the cursor — resume re-speaks the
  same paragraph from the beginning (paragraph-level granularity).
- Wake-word interrupts pause the worker with ``_paused_by_wake=True`` so
  the plugin can auto-resume after the assistant finishes responding.
- Stop / new-book / chapter navigation work cleanly without queue bleed.

Position is persisted on every successful paragraph completion via the
plugin's existing ``_save_state`` path so a Ctrl-C anywhere preserves the
last successfully-spoken paragraph.
"""
from __future__ import annotations

import threading
from typing import Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from plugins.ebook_reader.ebook_reader_plugin import EbookReaderPlugin


class BookPlaybackWorker:
    def __init__(self, plugin: "EbookReaderPlugin"):
        self._plugin = plugin
        self._stop = threading.Event()
        self._playing = threading.Event()       # set = playing, clear = paused
        self._item_done = threading.Event()     # set by EbookSpeechModule.on_item_done
        self._last_was_interrupted = False
        self._paused_by_wake = False            # set on system.interrupt_tts
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()           # guards position-mutation paths

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="BookPlaybackWorker")
        self._thread.start()
        logger.info("[BookWorker] Started")

    def stop(self) -> None:
        self._stop.set()
        # Unblock any waits
        self._playing.set()
        self._item_done.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("[BookWorker] Stopped")

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Resume (or start) playback from the saved position."""
        self._paused_by_wake = False
        self._playing.set()
        logger.info("[BookWorker] play")

    def pause(self) -> None:
        """Pause; current paragraph stays un-advanced and will replay on resume."""
        self._playing.clear()
        # Flush whatever is queued in the speech module so the current
        # paragraph stops playing at the next chunk boundary.
        self._flush_book_queue()
        logger.info("[BookWorker] pause")

    def is_playing(self) -> bool:
        return self._playing.is_set() and not self._stop.is_set()

    def was_paused_by_wake(self) -> bool:
        return self._paused_by_wake

    def stop_for_book_change(self) -> None:
        """Clear playback state when the user opens a different book."""
        self._playing.clear()
        self._paused_by_wake = False
        self._flush_book_queue()
        self._item_done.set()  # unblock the wait loop

    # ------------------------------------------------------------------
    # Wake-word interrupt entry point (called by the plugin)
    # ------------------------------------------------------------------

    def on_wake_interrupt(self) -> None:
        """Pause the book because the wake word fired."""
        if self.is_playing():
            self._paused_by_wake = True
            self._playing.clear()
            logger.info("[BookWorker] paused by wake-word interrupt")
        # Note: we don't flush our own queue here because the speech module's
        # own _on_interrupt handler already flushes its queue and aborts
        # playback. We just mark our intent to pause.

    def maybe_auto_resume(self) -> None:
        """Called when the main assistant finishes its response (listen_for_response).

        Resumes the book if and only if it was paused by a wake-word interrupt.
        """
        if self._paused_by_wake and not self._stop.is_set():
            logger.info("[BookWorker] auto-resuming after main TTS completed")
            self._paused_by_wake = False
            self._playing.set()

    # ------------------------------------------------------------------
    # Speech module callback
    # ------------------------------------------------------------------

    def on_item_done(self, _text: str, was_interrupted: bool) -> None:
        """Invoked by EbookSpeechModule after each queued paragraph finishes."""
        self._last_was_interrupted = was_interrupted
        self._item_done.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Block until we're playing
            if not self._playing.is_set():
                self._playing.wait(timeout=0.5)
                continue

            chapters = self._plugin._current_chapters()
            if not chapters:
                logger.info("[BookWorker] no chapters available — pausing")
                self._playing.clear()
                continue

            with self._lock:
                pos = self._plugin._state.get("position") or {}
                ch_idx = int(pos.get("chapter", 0))
                para_idx = int(pos.get("paragraph", 0))

            if ch_idx >= len(chapters):
                logger.info("[BookWorker] end of book — pausing")
                self._playing.clear()
                self._plugin._publish_reader_view()
                continue

            chapter = chapters[ch_idx]
            paragraphs = chapter.get("paragraphs", [])

            if para_idx >= len(paragraphs):
                # End of chapter — auto-advance to next chapter
                with self._lock:
                    self._plugin._state["position"] = {"chapter": ch_idx + 1, "paragraph": 0}
                    self._plugin._state_dirty = True
                    self._plugin._save_state()
                self._plugin._publish_reader_view()
                continue

            text = paragraphs[para_idx]
            if not text or not text.strip():
                # Empty paragraph — skip without speaking
                with self._lock:
                    self._plugin._state["position"]["paragraph"] = para_idx + 1
                    self._plugin._state_dirty = True
                    self._plugin._save_state()
                continue

            # Push to the speech module
            self._item_done.clear()
            self._last_was_interrupted = False
            self._plugin._book_tts_queue.put(text)

            # Update the reader view so the active paragraph highlights
            self._plugin._publish_reader_view()

            # Wait for the paragraph to finish, OR for pause/stop to fire
            while not self._stop.is_set():
                if self._item_done.wait(timeout=0.2):
                    break
                if not self._playing.is_set():
                    # Paused mid-speech — abandon waiting; position stays
                    # un-advanced so resume re-speaks this paragraph.
                    break

            if self._stop.is_set():
                break

            if not self._playing.is_set() or self._last_was_interrupted:
                # Either user paused us, or wake word interrupted: do not advance.
                # State save still happens through the normal pause path.
                logger.debug(f"[BookWorker] paragraph {ch_idx}.{para_idx} not advanced "
                             f"(playing={self._playing.is_set()}, interrupted={self._last_was_interrupted})")
                continue

            # Successfully spoken — persist the new position
            with self._lock:
                self._plugin._state["position"] = {"chapter": ch_idx, "paragraph": para_idx + 1}
                self._plugin._state_dirty = True
                self._plugin._save_state()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _flush_book_queue(self) -> None:
        """Drop any pending paragraphs from the book TTS queue."""
        import queue as _queue
        q = self._plugin._book_tts_queue
        flushed = 0
        while True:
            try:
                q.get_nowait()
                flushed += 1
            except _queue.Empty:
                break
        if flushed:
            logger.debug(f"[BookWorker] flushed {flushed} pending TTS items")
