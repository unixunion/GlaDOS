import asyncio
import queue
import re
import threading

import numpy as np
import sounddevice as sd
from loguru import logger
from num2words import num2words

from glados.config import GladosConfig
from glados.system.event_system import EventSystem, EventHook, EventMessage



class GladosSpeechModule:
    def __init__(self, tts, tts_queue: asyncio.Queue,
                 interruptible: bool = True,
                 speaking_lock: threading.Event = None,
                 config: GladosConfig = None):
        """
        Initializes the SpeechModule for TTS processing.

        Args:
            tts: The TTS synthesizer instance.
            tts_queue: The queue containing text to be processed into speech.
            interruptible: Whether the speech playback can be interrupted by new input (deprecated).
            speaking_lock: threading.Event, locked while speaking.
        """
        self._tts = tts
        self._tts_queue = tts_queue
        self.interruptible = interruptible  # deprecated
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self.event_system = EventSystem()
        self._speaking_lock = speaking_lock or threading.Event()  # Ensure a default Event is created
        self.stt_enabled = True
        self.config = config
        self._output_stream = None
        self._interrupted = threading.Event()  # Set when TTS should stop mid-playback

    def start(self):
        """
        Start the speech processing thread.
        """
        logger.info("Starting SpeechModule...")
        self._stop_event.clear()
        self._interrupted.clear()

        # Subscribe to interrupt events (wake word during TTS)
        if getattr(self.config, 'interrupt_on_wakeword', False):
            self.event_system.subscribe(
                "system.interrupt_tts",
                EventHook("tts_interrupt", callback=self._on_interrupt, priority=10)
            )
            logger.info("TTS interruption on wake word enabled")

        self._thread.start()

    def stop(self):
        """
        Stop the speech processing thread.
        """
        logger.info("Stopping SpeechModule...")
        self._stop_event.set()
        self._thread.join()
        if self._output_stream is not None:
            self._output_stream.close()
            self._output_stream = None

    def _on_interrupt(self, event: EventMessage):
        """Handle TTS interruption from wake word during speech."""
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

        # Stop any active audio output immediately
        if self._output_stream is not None and self._output_stream.active:
            try:
                self._output_stream.abort()
            except Exception as e:
                logger.debug(f"Error aborting audio stream: {e}")

        # Clear speaking lock so the mic activates
        self._speaking_lock.clear()
        self.event_system.publish(EventMessage("status", "idle", {"message": "Interrupted"}))

    def _process_queue(self):
        """
        Continuously process text from the TTS queue and play audio.
        """
        while not self._stop_event.is_set():
            try:
                # Wait for text from the queue with a timeout
                generated_text = self._tts_queue.get(timeout=0.1)

                # Check if we were interrupted while waiting
                if self._interrupted.is_set():
                    self._interrupted.clear()
                    # Drain anything left in the queue
                    while not self._tts_queue.empty():
                        try:
                            item = self._tts_queue.get_nowait()
                            if item == "<EOS>":
                                break
                        except queue.Empty:
                            break
                    self._speaking_lock.clear()
                    continue

                if generated_text == "<EOS>":  # End-of-stream signal
                    logger.info("Received end-of-stream signal. Clearing speaking lock.")
                    self._speaking_lock.clear()
                    self.event_system.publish(EventMessage("status", "idle", {"message": "Ready"}))
                    logger.info("Consider sending event to listen for response for a brief period, TODO")
                    self.event_system.publish(EventMessage(
                        "system",
                        "listen_for_response",
                        {}
                    ))
                    continue

                if not generated_text:
                    logger.warning("Empty string received for TTS processing.")
                    continue

                logger.debug(f"Locking speaking thread for TTS: {generated_text}")
                self._speaking_lock.set()  # Lock the speaking event
                self.event_system.publish(EventMessage("status", "speaking", {"message": generated_text[:80]}))
                processed_text = self._process_text(generated_text)
                self._say(processed_text)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in SpeechModule: {e}")

    def _split_long_text(self, text: str, max_len: int = 200) -> list:
        """Split long text into smaller chunks at sentence boundaries to avoid TTS model failures."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        # Split on sentence-ending punctuation followed by space
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
        """
        Generate and play TTS audio from text.
        """

        if text.strip() == "":
            return

        try:
            # Disable STT while TTS is playing
            if self.stt_enabled:
                # self.event_system.publish(EventMessage("system", "disable_stt", {}))
                self.stt_enabled = False

            # Split long text to avoid ONNX CoreML failures on large inputs
            chunks = self._split_long_text(text)
            for chunk in chunks:
                logger.info(f"Generating TTS for: {chunk}")
                audio = self._tts.generate_speech_audio(chunk)
                self._play_audio(audio)

        except Exception as e:
            logger.error(f"Error during TTS playback: {e}")
            # Note: speaking_lock is NOT cleared here — it stays set until EOS
            # is received in _process_queue, preventing the mic from picking up
            # TTS audio between sentences.

    def _ensure_output_stream(self):
        """Create or reuse a persistent output stream to avoid pops from stream open/close."""
        if self._output_stream is None or not self._output_stream.active:
            if self._output_stream is not None:
                self._output_stream.close()
            self._output_stream = sd.OutputStream(
                samplerate=self._tts.rate,
                channels=1,
                dtype='float32',
            )
            self._output_stream.start()
        return self._output_stream

    def _play_audio(self, audio):
        """
        Play the generated TTS audio using a persistent output stream.
        Stops early if interrupted by wake word.
        """
        if self._interrupted.is_set():
            return

        try:
            logger.debug("Playing TTS audio...")
            stream = self._ensure_output_stream()
            # Ensure audio is the right shape for the stream (N, 1)
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
        """
        Process text for better pronunciation and cleaner output for TTS.

        Args:
            text (str): The input text.

        Returns:
            str: The cleaned and processed text.
        """
        logger.debug(f"Original TTS text: {text}")

        # Replace numbers with words
        text = self._replace_numbers_with_words(text)

        # Filter out special tags
        text = self._filter_special_tags(text)

        # Normalize spacing and punctuation
        text = self._normalize_text(text)

        logger.debug(f"Processed TTS text: {text}")
        return text

    def _replace_numbers_with_words(self, text: str) -> str:
        """
        Replace numerical values in the text with their word equivalents,
        handling cases where numbers are part of words (e.g., "6pm" -> "six pm").
        """

        def number_to_words(match):
            number = match.group("number")
            prefix = match.group("prefix") or ""
            suffix = match.group("suffix") or ""
            # Convert the number to words
            number_word = num2words(int(number))
            # Reassemble the full word
            return f"{prefix}{number_word}{suffix}"

        # Regex to capture numbers with optional prefixes/suffixes (e.g., "6pm", "7:30")
        pattern = r"(?P<prefix>[^a-zA-Z\d])?(?P<number>\d+)(?P<suffix>[a-zA-Z]*)"

        return re.sub(pattern, number_to_words, text)

    def _filter_special_tags(self, text: str) -> str:
        """Filter out special tags or metadata from the text."""
        filtered_text = re.sub(
            r"(_[A-Z_]+_[A-Z_]+(?:\[[^\]]*\])?)|START_TOKEN_[A-Z_]+|END_TOKEN_[A-Z_]+",
            "",
            text
        )

        # remove text "(silence)"
        filtered_text = re.sub(r"\(silence\)", "", filtered_text)

        return filtered_text.strip()

    def _normalize_text(self, text: str) -> str:
        """Normalize spacing, punctuation, and line breaks for cleaner TTS output."""
        # Fix pronunciation of some words
        text = re.sub(r"(?i)\bplugins\b", "plug-ins", text)  # Match whole words case-insensitively
        text = re.sub(r"(?i)\bplugin\b", "plug-in", text)  # Match whole words case-insensitively
        text = re.sub(r"(?i)\bglados\b", "glad-oss", text)  # Pronunciation hint for TTS
        text = re.sub(r"%", " percent", text)  # Replace % with spoken form

        # Replace Unicode characters that crash the TTS phonemizer/ONNX model
        text = (
            text.replace("\u2014", ", ")   # em dash —
                .replace("\u2013", ", ")   # en dash –
                .replace("\u2018", "'")    # left single curly quote '
                .replace("\u2019", "'")    # right single curly quote '
                .replace("\u201c", '"')    # left double curly quote "
                .replace("\u201d", '"')    # right double curly quote "
                .replace("\u2026", "...")  # ellipsis …
        )

        # Remove extra whitespace and normalize punctuation
        text = (
            text.replace("\n\n", ". ")
                .replace("\n", ". ")
                .replace("  ", " ")
                .replace(":", " ")
                .replace("_", " ")
                .replace("*", " ")
        )
        text = re.sub(r"\s{2,}", " ", text).strip()  # Collapse extra spaces
        return text





