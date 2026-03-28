import queue
import re
import threading
from abc import ABC, abstractmethod

import numpy as np
import sounddevice as sd
from loguru import logger
from num2words import num2words

from glados.config import GladosConfig
from glados.system.event_system import EventSystem, EventHook, EventMessage


class SpeechModule(ABC):
    """Abstract base class for TTS voice cores.

    Subclasses only need to implement _synthesize() to convert text to audio.
    All queue processing, text preprocessing, interrupt handling, and audio
    playback is handled here.
    """

    def __init__(self, tts_queue, speaking_lock: threading.Event = None,
                 config: GladosConfig = None):
        self._tts_queue = tts_queue
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self.event_system = EventSystem()
        self._speaking_lock = speaking_lock or threading.Event()
        self.config = config
        self._output_stream = None
        self._interrupted = threading.Event()

    @abstractmethod
    def _synthesize(self, text: str) -> np.ndarray:
        """Generate audio from text. Returns numpy float32 array."""

    @abstractmethod
    def _get_sample_rate(self) -> int:
        """Return the sample rate for audio output."""

    def start(self):
        logger.info("Starting SpeechModule...")
        self._stop_event.clear()
        self._interrupted.clear()

        self.event_system.subscribe(
            "system.interrupt_tts",
            EventHook("tts_interrupt", callback=self._on_interrupt, priority=10)
        )
        logger.info("TTS interruption enabled")

        self._thread.start()

    def stop(self):
        logger.info("Stopping SpeechModule...")
        self._stop_event.set()
        self._thread.join()
        if self._output_stream is not None:
            self._output_stream.close()
            self._output_stream = None

    def _on_interrupt(self, event: EventMessage):
        """Handle TTS interruption from wake word or UI stop button.

        IMPORTANT: Do NOT touch _output_stream here. This handler runs on the
        EventSystem/SocketIO thread while _play_audio may be blocked inside
        stream.write() on the TTS thread. Closing or aborting the stream from
        another thread causes a segfault in PortAudio. Instead, just set the
        _interrupted flag — the TTS thread checks it between chunks and after
        write() returns (or raises), then handles stream cleanup itself.
        """
        logger.info("TTS interrupted by wake word")
        self._interrupted.set()

        # Flush remaining text from the TTS queue
        flushed = 0
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
                flushed += 1
            except queue.Empty:
                break
        if flushed:
            logger.info(f"Flushed {flushed} queued TTS item(s)")

        # Clear speaking lock so the mic activates
        self._speaking_lock.clear()
        self.event_system.publish(EventMessage("status", "idle", {"message": "Interrupted"}))

    def _process_queue(self):
        while not self._stop_event.is_set():
            try:
                generated_text = self._tts_queue.get(timeout=0.1)

                # Check if we were interrupted while waiting
                if self._interrupted.is_set():
                    self._interrupted.clear()
                    # Close the stream on the TTS thread (safe — no concurrent write)
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
                    continue

                if generated_text == "<EOS>":
                    logger.info("Received end-of-stream signal. Clearing speaking lock.")
                    self._speaking_lock.clear()
                    self.event_system.publish(EventMessage("status", "idle", {"message": "Ready"}))
                    self.event_system.publish(EventMessage("system", "listen_for_response", {}))
                    continue

                if not generated_text:
                    logger.warning("Empty string received for TTS processing.")
                    continue

                logger.debug(f"Locking speaking thread for TTS: {generated_text}")
                self._speaking_lock.set()
                self.event_system.publish(EventMessage("status", "speaking", {"message": generated_text[:80]}))
                self.event_system.publish(EventMessage("chat", "spoken", {"role": "spoken", "content": generated_text}))
                processed_text = self._process_text(generated_text)
                self._say(processed_text)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in SpeechModule: {e}")

    def _split_long_text(self, text: str, max_len: int = 200) -> list:
        """Split long text into smaller chunks at sentence boundaries."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        sentences = re.split(r'(?<=[.!?])\s+', text)
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > max_len:
                chunks.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip() if current else sentence
        if current.strip():
            chunks.append(current.strip())
        return chunks

    def _say(self, text: str):
        if text.strip() == "":
            return

        try:
            chunks = self._split_long_text(text)
            for chunk in chunks:
                if self._interrupted.is_set():
                    return
                logger.info(f"Generating TTS for: {chunk}")
                audio = self._synthesize(chunk)
                self._play_audio(audio)
        except Exception as e:
            logger.error(f"Error during TTS playback: {e}")

    def _ensure_output_stream(self):
        """Create or reuse a persistent output stream to avoid pops."""
        if self._output_stream is None or not self._output_stream.active:
            if self._output_stream is not None:
                self._output_stream.close()
            self._output_stream = sd.OutputStream(
                samplerate=self._get_sample_rate(),
                channels=1,
                dtype='float32',
            )
            self._output_stream.start()
        return self._output_stream

    def _play_audio(self, audio):
        if self._interrupted.is_set():
            return

        try:
            logger.debug("Playing TTS audio...")
            stream = self._ensure_output_stream()
            audio = np.asarray(audio, dtype=np.float32)
            if audio.ndim == 1:
                audio = audio.reshape(-1, 1)
            stream.write(audio)
        except Exception as e:
            if self._interrupted.is_set():
                logger.debug("Audio playback aborted by interrupt")
            else:
                logger.error(f"Error during audio playback: {e}")

    def _process_text(self, text: str) -> str:
        logger.debug(f"Original TTS text: {text}")
        text = self._replace_numbers_with_words(text)
        text = self._filter_special_tags(text)
        text = self._normalize_text(text)
        logger.debug(f"Processed TTS text: {text}")
        return text

    def _replace_numbers_with_words(self, text: str) -> str:
        def number_to_words(match):
            number = match.group("number")
            prefix = match.group("prefix") or ""
            suffix = match.group("suffix") or ""
            number_word = num2words(int(number))
            return f"{prefix}{number_word}{suffix}"

        pattern = r"(?P<prefix>[^a-zA-Z\d])?(?P<number>\d+)(?P<suffix>[a-zA-Z]*)"
        return re.sub(pattern, number_to_words, text)

    def _filter_special_tags(self, text: str) -> str:
        filtered_text = re.sub(
            r"(_[A-Z_]+_[A-Z_]+(?:\[[^\]]*\])?)|START_TOKEN_[A-Z_]+|END_TOKEN_[A-Z_]+",
            "",
            text
        )
        filtered_text = re.sub(r"\(silence\)", "", filtered_text)
        return filtered_text.strip()

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r"(?i)\bplugins\b", "plug-ins", text)
        text = re.sub(r"(?i)\bplugin\b", "plug-in", text)
        text = re.sub(r"(?i)\bglados\b", "glad-oss", text)
        text = re.sub(r"%", " percent", text)
        # Expand cooking abbreviations for natural speech
        text = re.sub(r"(?i)\bTbsp\.?\b", "tablespoon", text)
        text = re.sub(r"(?i)\btbsps\.?\b", "tablespoons", text)
        text = re.sub(r"(?i)\btsp\.?\b", "teaspoon", text)
        text = re.sub(r"(?i)\btsps\.?\b", "teaspoons", text)
        text = re.sub(r"(?i)\boz\.?\b", "ounce", text)
        text = re.sub(r"(?i)\blbs?\.?\b", "pounds", text)
        text = re.sub(r"(?i)\bpkg\.?\b", "package", text)
        text = re.sub(r"(?i)\bqt\.?\b", "quart", text)
        text = re.sub(r"(?i)\bpt\.?\b", "pint", text)

        text = (
            text.replace("\u2014", ", ")
                .replace("\u2013", ", ")
                .replace("\u2018", "'")
                .replace("\u2019", "'")
                .replace("\u201c", '"')
                .replace("\u201d", '"')
                .replace("\u2026", "...")
        )

        text = (
            text.replace("\n\n", ". ")
                .replace("\n", ". ")
                .replace("  ", " ")
                .replace(":", " ")
                .replace("_", " ")
                .replace("*", " ")
        )
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text
