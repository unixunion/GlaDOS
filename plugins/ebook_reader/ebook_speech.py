"""Book-narrator voice core — a :class:`KokoroSpeechModule` subclass.

Two behavioral overrides vs. the base Kokoro module:

1. Suppresses the ``system.listen_for_response`` event normally published on
   ``<EOS>``. End-of-chapter is not a user prompt, and we don't want the
   assistant to drop into mic-listen mode after every chapter.

2. Invokes an ``on_item_done(text, was_interrupted)`` callback after each
   queued paragraph finishes (or is interrupted). The :class:`BookPlaybackWorker`
   uses this to advance the saved reading position only when a paragraph
   completes successfully — interrupted paragraphs stay un-advanced so they
   replay on resume.

The whole ``_process_queue`` body is overridden because the parent class
hardcodes the ``listen_for_response`` publish. Most of the body is copied
verbatim from :class:`SpeechModule._process_queue`.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, Optional

from loguru import logger

from glados.llm.voice_cores.kokoro_speech_module import KokoroSpeechModule
from glados.system.event_system import EventMessage


class EbookSpeechModule(KokoroSpeechModule):
    """KokoroSpeechModule variant tailored for book narration."""

    def __init__(self, tts, tts_queue, speaking_lock: threading.Event = None,
                 config=None, on_item_done: Optional[Callable[[str, bool], None]] = None):
        super().__init__(tts=tts, tts_queue=tts_queue,
                         speaking_lock=speaking_lock, config=config)
        self._on_item_done = on_item_done

    def _process_queue(self):
        # Adapted from glados.llm.voice_cores.speech_module.SpeechModule._process_queue:
        #   - suppresses system.listen_for_response on <EOS>
        #   - invokes self._on_item_done after each spoken (or interrupted) item
        #   - omits the chat.spoken event publish (book narration shouldn't appear in chat)
        import time as _time

        while not self._stop_event.is_set():
            try:
                generated_text = self._tts_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                # Interrupt drain — copy of parent behavior
                if self._interrupted.is_set():
                    self._interrupted.clear()
                    if self._output_stream is not None:
                        try:
                            self._output_stream.abort()
                            self._output_stream.close()
                        except Exception:
                            pass
                        self._output_stream = None
                    while not self._tts_queue.empty():
                        try:
                            item = self._tts_queue.get_nowait()
                            if item == "<EOS>":
                                break
                        except queue.Empty:
                            break
                    self._speaking_lock.clear()
                    # Notify the worker that the in-flight item was interrupted
                    if self._on_item_done is not None:
                        try:
                            self._on_item_done(generated_text or "", True)
                        except Exception as e:
                            logger.warning(f"[EbookSpeech] on_item_done(interrupted) raised: {e}")
                    continue

                if generated_text == "<EOS>":
                    _time.sleep(0.15)
                    self._speaking_lock.clear()
                    self.event_system.publish(EventMessage("status", "idle", {"message": "Book idle"}))
                    # NOTE: deliberately NOT publishing system.listen_for_response —
                    # books shouldn't drop the assistant into a listening state.
                    continue

                if not generated_text:
                    continue

                logger.debug(f"[EbookSpeech] speaking: {generated_text[:80]}")
                self._speaking_lock.set()
                self.event_system.publish(EventMessage(
                    "status", "speaking", {"message": generated_text[:80], "source": "ebook"},
                ))
                # Skip the chat.spoken publish — book paragraphs don't belong in chat history.

                processed_text = self._process_text(generated_text)
                self._say(processed_text)

                # _say() returns when audio playback completes OR when an interrupt
                # fires mid-chunk. If we were interrupted, the flag is still set.
                was_interrupted = self._interrupted.is_set()

                if self._on_item_done is not None:
                    try:
                        self._on_item_done(generated_text, was_interrupted)
                    except Exception as e:
                        logger.warning(f"[EbookSpeech] on_item_done raised: {e}")

            except Exception as e:
                logger.error(f"[EbookSpeech] Error in queue loop: {e}")
