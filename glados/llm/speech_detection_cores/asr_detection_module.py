import queue
import threading
import time
from typing import Optional
import sounddevice as sd

import numpy as np
from loguru import logger


class AsrVoiceDetectionModule:
    def __init__(
        self,
        vad_model,
        asr_model,
        client_queue: queue.Queue,
        wake_word: Optional[str] = None,
        sample_rate: int = 16000,
        vad_chunk_size_ms: int = 30,
        buffer_size_ms: int = 3000,
        vad_threshold: float = 0.5,
        similarity_threshold: int = 3,
    ):
        self.vad_model = vad_model
        self.asr_model = asr_model
        self.client_queue = client_queue
        self.wake_word = wake_word
        self.sample_rate = sample_rate
        self.vad_chunk_size = vad_chunk_size_ms
        self.buffer_size = buffer_size_ms // vad_chunk_size_ms
        self.vad_threshold = vad_threshold
        self.similarity_threshold = similarity_threshold

        self.buffer = queue.Queue(maxsize=self.buffer_size)
        self.samples = []
        self.recording_started = False
        self.gap_counter = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.lock = threading.Lock()

        logger.info("VoiceDetectionModule initialized with:")
        logger.info(f"  Sample rate: {self.sample_rate} Hz")
        logger.info(f"  VAD chunk size: {self.vad_chunk_size} ms")
        logger.info(f"  Buffer size: {self.buffer_size * self.vad_chunk_size} ms")
        logger.info(f"  VAD threshold: {self.vad_threshold}")
        logger.info(f"  Similarity threshold: {self.similarity_threshold}")
        if self.wake_word:
            logger.info(f"  Wake word: '{self.wake_word}'")

    def start(self):
        """Start the voice detection thread."""
        if self.thread.is_alive():
            logger.warning("VoiceDetectionModule is already running.")
            return
        logger.info("Starting VoiceDetectionModule...")
        self.stop_event.clear()
        self.thread.start()

    def stop(self):
        """Stop the voice detection thread."""
        logger.info("Stopping VoiceDetectionModule...")
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join()
        logger.info("VoiceDetectionModule stopped.")

    def _run(self):
        """Main processing loop for voice detection."""
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                callback=self._audio_callback,
                blocksize=int(self.sample_rate * self.vad_chunk_size / 1000),
            ):
                logger.info("Voice detection stream started.")
                while not self.stop_event.is_set():
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in VoiceDetectionModule stream: {e}")

    def _audio_callback(self, indata, frames, time, status):
        """Audio callback for processing audio chunks."""
        try:
            if status:
                logger.warning(f"Audio callback status: {status}")
            data = indata.copy().squeeze()
            vad_value = self.vad_model.process_chunk(data)
            vad_confidence = vad_value > self.vad_threshold

            logger.debug(f"VAD confidence: {vad_value}, Threshold exceeded: {vad_confidence}")

            gap_counter = None
            with self.lock:
                if not self.recording_started:
                    self._manage_pre_activation_buffer(data, vad_confidence)
                else:
                    # Collect the sample without locking during processing
                    self.samples.append(data)
                    gap_counter = self._process_activated_audio(vad_confidence)

            # Handle finalization outside the lock
            if gap_counter is not None and gap_counter >= self.buffer_size:
                logger.info("No voice activity detected. Finalizing audio.")
                self._process_detected_audio()
        except Exception as e:
            logger.error(f"Error in audio callback: {e}")

    def _manage_pre_activation_buffer(self, sample: np.ndarray, vad_confidence: bool):
        """Buffer audio samples until activation."""
        if self.buffer.full():
            discarded = self.buffer.get()  # Discard the oldest sample to make room
            logger.debug("Buffer full. Discarding oldest sample.")

        self.buffer.put(sample)
        logger.debug(f"Added sample to buffer. Current size: {self.buffer.qsize()}")

        if vad_confidence:
            logger.info("Voice activity detected. Activating recording.")
            self.samples = list(self.buffer.queue)
            self.recording_started = True

    def _process_activated_audio(self, vad_confidence: bool) -> Optional[int]:
        """Process audio after activation."""
        if not vad_confidence:
            self.gap_counter += 1
            logger.debug(f"Gap counter: {self.gap_counter}/{self.buffer_size}")
            return self.gap_counter
        else:
            self.gap_counter = 0
            return None

    def _process_detected_audio(self):
        """Process detected audio and send to LLM client."""
        try:
            # No need to hold the lock during transcription
            audio = np.concatenate(self.samples)
            logger.info("Transcribing audio...")
            detected_text = self.asr_model.transcribe(audio)
            logger.info(f"Transcribed text: {detected_text}")

            if detected_text:
                if self.wake_word and not self._wakeword_detected(detected_text):
                    logger.info(f"Wake word '{self.wake_word}' not detected.")
                    return
                logger.info("Sending text to LLM client.")
                self.client_queue.put(detected_text)
            else:
                logger.warning("No text detected from audio.")
        except Exception as e:
            logger.error(f"Error processing detected audio: {e}")
        finally:
            self.reset()

    def _wakeword_detected(self, text: str) -> bool:
        """Check if wake word is detected."""
        from Levenshtein import distance

        words = text.split()
        closest_distance = min(distance(word.lower(), self.wake_word.lower()) for word in words)
        logger.debug(f"Closest wake word distance: {closest_distance}")
        return closest_distance <= self.similarity_threshold

    def reset(self):
        """Reset the module state."""
        logger.info("reset called")
        with self.lock:
            logger.info("Resetting module state.")
            self.samples.clear()
            self.gap_counter = 0
            self.recording_started = False
            while not self.buffer.empty():
                self.buffer.get()
            logger.info("module reset")
