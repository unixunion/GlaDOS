import queue
import threading
import time
from typing import Optional
import sounddevice as sd
import numpy as np
from loguru import logger
import whisper

import glados.config
from glados.config import VAD_THRESHOLD
from glados.system.event_system import EventSystem, EventMessage, EventHook


class WhisperVoiceDetectionModule:
    def __init__(
            self,
            vad_model,
            client_queue: queue.Queue,
            wake_word: Optional[str] = None,
            sample_rate: int = glados.config.SAMPLE_RATE,
            vad_chunk_size_ms: int = glados.config.VAD_SIZE,
            buffer_size_ms: int = glados.config.BUFFER_SIZE,
            vad_threshold: float = VAD_THRESHOLD,
            similarity_threshold: int = glados.config.SIMILARITY_THRESHOLD,
            whisper_model_size: str = "base",
            interrupt_event: threading.Event = None,
            speaking_lock: threading.Event = None,
            wakeword_module=None,
    ):
        self.vad_model = vad_model
        self.wakeword_module = wakeword_module
        self.client_queue = client_queue
        self.wake_word = wake_word
        self.sample_rate = sample_rate
        self.vad_chunk_size = vad_chunk_size_ms
        self.buffer_size = buffer_size_ms // vad_chunk_size_ms
        self.vad_threshold = vad_threshold
        self.similarity_threshold = similarity_threshold
        self.listening_enabled = False
        self.pre_wake_buffer = queue.Queue(maxsize=self.buffer_size)
        self.interrupt_event = interrupt_event
        self.speaking_lock = speaking_lock
        self.samples = []
        self.recording_started = False
        self.gap_counter = 0
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        self.event_system = EventSystem()
        self.event_system.subscribe(
            "system.listen_for_response",
            EventHook(name="listen_for_response", callback=self.listen_for_response, priority=1)
        )

        # Whisper ASR Model
        logger.info(f"Loading Whisper model: {whisper_model_size}")
        self.asr_model = whisper.load_model(whisper_model_size)

        # Audio Thread
        self.thread = threading.Thread(target=self._run, daemon=True)

        logger.info("VoiceDetectionModule initialized with:")
        logger.info(f"  Sample rate: {self.sample_rate} Hz")
        logger.info(f"  VAD chunk size: {self.vad_chunk_size} ms")
        logger.info(f"  Buffer size: {self.buffer_size * self.vad_chunk_size} ms")
        logger.info(f"  VAD threshold: {self.vad_threshold}")

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
            # Initialize the audio stream
            with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    callback=self._audio_callback,
                    blocksize=int(self.sample_rate * self.vad_chunk_size / 1000),
            ):
                logger.info("Voice detection stream started.")

                while not self.stop_event.is_set():
                    # Check if interrupt_event is set to activate listening
                    if self.interrupt_event.is_set():
                        logger.info("Interrupt event detected. Enabling listening.")
                        self.enable_listening()
                        self.interrupt_event.clear()

                    time.sleep(0.1)  # Keep the thread alive
        except Exception as e:
            logger.error(f"Error in VoiceDetectionModule stream: {e}")
        finally:
            logger.info("Voice detection thread exited.")

    def listen_for_response(self, event: EventMessage):
        logger.info("TODO Implement listen for response here")

    def _audio_callback(self, indata, frames, time, status):
        """Audio callback for processing audio chunks."""
        try:
            if status:
                logger.warning(f"Audio callback status: {status}")
            data = indata.copy().squeeze()

            # Forward audio to wake word module (always, even during speaking lock)
            if self.wakeword_module is not None:
                self.wakeword_module.push_audio(data.copy())

            # Skip processing if speaking lock is set
            if self.speaking_lock.is_set():
                logger.debug("Speaking lock is active. Ignoring audio input.")
                return

            # Buffer pre-wake audio if listening is disabled
            if not self.listening_enabled:
                if self.pre_wake_buffer.full():
                    self.pre_wake_buffer.get()
                self.pre_wake_buffer.put(data)
                return

            # Process audio for VAD and activation
            vad_value = self.vad_model.process_chunk(data)
            vad_confidence = vad_value > self.vad_threshold

            logger.debug(f"VAD confidence: {vad_value}, Threshold exceeded: {vad_confidence}")

            with self.lock:
                if not self.recording_started:
                    if vad_confidence:
                        logger.info("Voice activity detected. Activating recording.")
                        self.samples = list(self.pre_wake_buffer.queue)
                        self.recording_started = True
                else:
                    self.samples.append(data)
                    if not vad_confidence:
                        self.gap_counter += 1
                    else:
                        self.gap_counter = 0

            # Finalize audio if silence persists
            if self.gap_counter >= self.buffer_size:
                logger.info("No voice activity detected. Finalizing audio.")
                self._process_detected_audio()

        except Exception as e:
            logger.error(f"Error in audio callback: {e}")

    def _process_detected_audio(self):
        """Process detected audio and send to LLM client."""
        try:
            audio = np.concatenate(self.samples)
            logger.info("Transcribing audio using Whisper...")
            audio_normalized = audio / np.max(np.abs(audio))
            detected_text = self.asr_model.transcribe(audio_normalized, fp16=False, language="en")["text"]

            logger.info(f"Transcribed text: {detected_text}")

            if detected_text:
                self.client_queue.put(detected_text)
            else:
                logger.warning("No text detected from audio.")
        except Exception as e:
            logger.error(f"Error processing detected audio: {e}")
        finally:
            self.reset()

    def enable_listening(self):
        """Enable audio processing."""
        logger.info("Enabling listening.")
        self.listening_enabled = True
        self.gap_counter = 0
        self.samples.clear()

    def disable_listening(self):
        """Disable audio processing."""
        logger.info("Disabling listening.")
        self.listening_enabled = False

    def reset(self):
        """Reset the module state."""
        logger.info("Resetting module state.")
        with self.lock:
            self.samples.clear()
            self.gap_counter = 0
            self.recording_started = False
            while not self.pre_wake_buffer.empty():
                self.pre_wake_buffer.get()

