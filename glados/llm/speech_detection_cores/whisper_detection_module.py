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
from plugins.event_system.event_system import EventSystem, EventMessage, EventHook


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
    ):
        self.vad_model = vad_model
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

    # def _audio_callback(self, indata, frames, time, status):
    #     """Audio callback for processing audio chunks."""
    #     try:
    #         if status:
    #             logger.warning(f"Audio callback status: {status}")
    #         data = indata.copy().squeeze()
    #
    #         # Buffer pre-wake audio
    #         if not self.listening_enabled:
    #             if self.pre_wake_buffer.full():
    #                 self.pre_wake_buffer.get()
    #             self.pre_wake_buffer.put(data)
    #             return
    #
    #         # Process audio if listening is enabled
    #         vad_value = self.vad_model.process_chunk(data)
    #         vad_confidence = vad_value > self.vad_threshold
    #
    #         logger.debug(f"VAD confidence: {vad_value}, Threshold exceeded: {vad_confidence}")
    #
    #         with self.lock:
    #             if not self.recording_started:
    #                 if vad_confidence:
    #                     logger.info("Voice activity detected. Activating recording.")
    #                     self.samples = list(self.pre_wake_buffer.queue)
    #                     self.recording_started = True
    #             else:
    #                 self.samples.append(data)
    #                 if not vad_confidence:
    #                     self.gap_counter += 1
    #                 else:
    #                     self.gap_counter = 0
    #
    #         # Finalize audio if silence persists
    #         if self.gap_counter >= self.buffer_size:
    #             logger.info("No voice activity detected. Finalizing audio.")
    #             self._process_detected_audio()
    #     except Exception as e:
    #         logger.error(f"Error in audio callback: {e}")

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

# class WhisperVoiceDetectionModule:
#     def __init__(
#             self,
#             vad_model,
#             client_queue: queue.Queue,
#             wake_word: Optional[str] = None,
#             sample_rate: int = glados.config.SAMPLE_RATE,  # 16000
#             vad_chunk_size_ms: int = glados.config.VAD_SIZE,  # 50ms
#             buffer_size_ms: int = glados.config.BUFFER_SIZE,  # 700ms
#             vad_threshold: float = VAD_THRESHOLD,  # 0.9
#             similarity_threshold: int = glados.config.SIMILARITY_THRESHOLD,
#             whisper_model_size: str = "base",
#             interrupt_event: threading.Event = None,
#             speaking_lock: threading.Event = None
#     ):
#         self.vad_model = vad_model
#         self.client_queue = client_queue
#         self.wake_word = wake_word
#         self.sample_rate = sample_rate
#         self.vad_chunk_size = vad_chunk_size_ms
#         self.buffer_size = buffer_size_ms // vad_chunk_size_ms
#         self.vad_threshold = vad_threshold
#         self.similarity_threshold = similarity_threshold
#         self._speaking_lock = speaking_lock
#         self.listening_enabled = False
#         self.pre_wake_buffer = queue.Queue(maxsize=self.buffer_size)
#
#         # Initialize Whisper model
#         logger.info(f"Loading Whisper model: {whisper_model_size}")
#         self.asr_model = whisper.load_model(whisper_model_size)
#         self.interrupt_event = interrupt_event
#         # self.listen_override_event = threading.Event()
#
#         self.buffer = queue.Queue(maxsize=self.buffer_size)
#         self.samples = []
#         self.recording_started = False
#         self.gap_counter = 0
#         self.stop_event = threading.Event()
#         self.thread = threading.Thread(target=self._run, daemon=True)
#         self.lock = threading.Lock()
#
#         # variables for overriding recording for interactive responses after a system.listen_for_response event
#         self.override_fixed_window_ms = 2000  # Fixed 2-second recording window
#         self.override_fixed_window_frames = self.override_fixed_window_ms // self.vad_chunk_size
#         self.override_counter = 0
#         self.in_fixed_window = False  # Track if currently in fixed recording window
#
#         logger.info("VoiceDetectionModule initialized with:")
#         logger.info(f"  Sample rate: {self.sample_rate} Hz")
#         logger.info(f"  VAD chunk size: {self.vad_chunk_size} ms")
#         logger.info(f"  Buffer size: {self.buffer_size * self.vad_chunk_size} ms")
#         logger.info(f"  VAD threshold: {self.vad_threshold}")
#         logger.info(f"  Similarity threshold: {self.similarity_threshold}")
#
#         # using the event system we can initiate recordings for interactive responses without the wakeword
#         self.event_system = EventSystem()
#         self.event_system.subscribe(
#             "system.listen_for_response",
#             EventHook(name="listen_for_response", callback=self.listen_for_response, priority=1)
#         )
#         self.event_system.subscribe(
#             "system.disable_stt",
#             EventHook(name="disable_stt", callback=self._disable_stt, priority=1)
#         )
#         self.event_system.subscribe(
#             "system.enable_stt",
#             EventHook(name="enable_stt", callback=self._enable_stt, priority=1)
#         )
#         self.event_system.subscribe(
#             "system.wake_word_detected",
#             EventHook(name="wake_word_detected", callback=self._on_wake_word_detected, priority=1)
#         )
#
#     def _on_wake_word_detected(self, event: EventMessage):
#         """Handle wake word detected events."""
#         logger.info("Wake word detected! Preparing to listen for speech.")
#         self.enable_listening()  # Enable audio processing
#
#         # Append pre-wake buffer contents to samples without clearing prematurely
#         with self.lock:
#             while not self.pre_wake_buffer.empty():
#                 self.samples.append(self.pre_wake_buffer.get())
#
#         self.recording_started = True
#         logger.info(f"Pre-wake buffer appended to samples. Current sample size: {len(self.samples)}")
#
#     # def _on_wake_word_detected(self, event: EventMessage):
#     #     """Handle wake word detected events."""
#     #     logger.info("Wake word detected! Preparing to listen for speech.")
#     #     # self.listen_override_event.set()
#     #     self.enable_listening()  # Enable audio processing
#     #     # new attempt at better wake word
#     #     self.samples = list(self.pre_wake_buffer.queue)  # Include pre-wake audio
#     #     self.pre_wake_buffer.queue.clear()  # Clear the pre-wake buffer
#     #     # self.reset()
#     #     self.recording_started = True
#
#     def listen_for_response(self, event: EventMessage):
#         logger.info("Preparing to listen for response.")
#         self.enable_listening()
#         self.reset()
#         # logger.info("waiting for speech to stop")
#         #
#         # if not self.listen_override_event.is_set():
#         #     logger.info("Setting listen_override interrupt")
#         #     self.listen_override_event.set()
#         #     self.reset()
#         # else:
#         #     logger.warning("ignoring additional request to listen for response, already listening.")
#
#     def start(self):
#         """Start the voice detection thread."""
#         if self.thread.is_alive():
#             logger.warning("VoiceDetectionModule is already running.")
#             return
#         logger.info("Starting VoiceDetectionModule...")
#         self.stop_event.clear()
#         self.thread.start()
#
#     def stop(self):
#         """Stop the voice detection thread."""
#         logger.info("Stopping VoiceDetectionModule...")
#         self.stop_event.set()
#         if self.thread.is_alive():
#             self.thread.join()
#         logger.info("VoiceDetectionModule stopped.")
#
#     def _run(self):
#         """Main processing loop for voice detection."""
#         try:
#             # Initialize the audio stream
#             with sd.InputStream(
#                     samplerate=self.sample_rate,
#                     channels=1,
#                     callback=self._audio_callback,
#                     blocksize=int(self.sample_rate * self.vad_chunk_size / 1000),
#             ):
#                 logger.info("Voice detection stream started.")
#
#                 # Keep listening until explicitly stopped
#                 while not self.stop_event.is_set():
#                     time.sleep(0.1)  # Keep the thread alive
#         except Exception as e:
#             logger.error(f"Error in VoiceDetectionModule stream: {e}")
#         finally:
#             logger.info("Voice detection thread exited.")
#
#     def _audio_callback(self, indata, frames, time, status):
#         """Audio callback for processing audio chunks."""
#         try:
#             if status:
#                 logger.warning(f"Audio callback status: {status}")
#             data = indata.copy().squeeze()
#
#             # Continuously buffer audio before wake word detection
#             if not self.listening_enabled:
#                 if self.pre_wake_buffer.full():
#                     self.pre_wake_buffer.get()  # Remove the oldest chunk
#                 self.pre_wake_buffer.put(data)
#                 return
#             # if not self.listening_enabled:
#             #     logger.debug("Listening disabled. Skipping audio processing.")
#             #     return
#
#             # Process audio for VAD and wake word detection
#             vad_value = self.vad_model.process_chunk(data)
#             vad_confidence = vad_value > self.vad_threshold
#
#             logger.debug(f"VAD confidence: {vad_value}, Threshold exceeded: {vad_confidence}")
#
#             gap_counter = None
#             with self.lock:
#                 if not self.recording_started:
#                     self._manage_pre_activation_buffer(data, vad_confidence)
#                 else:
#                     self.samples.append(data)
#                     gap_counter = self._process_activated_audio(vad_confidence)
#
#             if gap_counter is not None and gap_counter >= self.buffer_size:
#                 logger.info("No voice activity detected. Finalizing audio.")
#                 self._process_detected_audio()
#         except Exception as e:
#             logger.error(f"Error in audio callback: {e}")
#
#     def _process_detected_audio(self):
#         """Process detected audio and send to LLM client."""
#         try:
#             audio = np.concatenate(self.samples)
#             logger.info("Transcribing audio using Whisper...")
#             # Whisper expects 16-bit float audio normalized to [-1, 1]
#             audio_normalized = audio / np.max(np.abs(audio))
#             detected_text = self.asr_model.transcribe(audio_normalized, fp16=False, language="en")["text"]
#             logger.info(f"Transcribed text: {detected_text}")
#
#             if detected_text:
#                 logger.info("Sending text to LLM client.")
#                 self.client_queue.put(detected_text)
#             else:
#                 logger.warning("No text detected from audio.")
#         except Exception as e:
#             logger.error(f"Error processing detected audio: {e}")
#         finally:
#             self.reset()
#
#     def _wakeword_detected(self, text: str) -> bool:
#         """Check if wake word is detected."""
#         from Levenshtein import distance
#
#         words = text.split()
#         closest_distance = min(distance(word.lower(), self.wake_word.lower()) for word in words)
#         logger.debug(f"Closest wake word distance: {closest_distance}")
#         return closest_distance <= self.similarity_threshold
#
#     def reset(self):
#         """Reset the module state."""
#         logger.info("Resetting module state.")
#         with self.lock:
#             self.samples.clear()
#             self.gap_counter = 0
#             self.override_counter = 0
#             self.recording_started = False
#             self.in_fixed_window = False  # Ensure fixed window is reset
#             while not self.buffer.empty():
#                 self.buffer.get()
#             logger.info("Module reset.")
#
#     def _manage_pre_activation_buffer(self, sample: np.ndarray, vad_confidence: bool):
#         """Buffer audio samples until activation."""
#         if self.buffer.full():
#             discarded = self.buffer.get()
#             logger.debug("Buffer full. Discarding oldest sample.")
#
#         self.buffer.put(sample)
#         logger.debug(f"Added sample to buffer. Current size: {self.buffer.qsize()}")
#
#         # Check if an override or interrupt event was triggered
#         # if self.listen_override_event.is_set() or self.interrupt_event.is_set():
#         #     logger.info("Override/Interrupt event triggered. Starting fixed recording window.")
#         #     self.listen_override_event.clear()
#         #     self.interrupt_event.clear()
#         #     self._activate_fixed_window()
#
#         if self.interrupt_event.is_set():
#             logger.info("interrupt is set, clearing")
#             self.interrupt_event.clear()
#
#         # Activate recording if voice activity is detected
#         if vad_confidence and self.recording_started:
#             logger.info("Voice activity detected. Activating recording.")
#             self._activate_buffer()
#
#     def _enable_stt(self, event: EventMessage):
#         """Enable STT by allowing audio processing."""
#         logger.info("Enabling STT input stream.")
#         self.enable_listening()
#
#     def _disable_stt(self, event: EventMessage):
#         """Disable STT by preventing audio processing."""
#         logger.info("Disabling STT input stream.")
#         self.disable_listening()
#
#     def _activate_buffer(self):
#         self.samples = list(self.buffer.queue)
#         self.recording_started = True
#
#     def _activate_fixed_window(self):
#         """Activate the fixed recording window."""
#
#         # if self._speaking_lock.is_set():
#         #     logger.warning("Cannot activate fixed window while TTS is speaking.")
#         #     return
#
#         self.samples = list(self.buffer.queue)
#         self.recording_started = True
#         self.in_fixed_window = True
#         self.override_counter = 0  # Reset override counter
#
#     def _process_activated_audio(self, vad_confidence: bool) -> Optional[int]:
#         """Process audio after activation."""
#         if self.in_fixed_window:
#             self.override_counter += 1
#             logger.debug(f"Fixed window active: {self.override_counter}/{self.override_fixed_window_frames}")
#
#             # Transition out of the fixed window after its duration
#             if self.override_counter >= self.override_fixed_window_frames:
#                 logger.info("Fixed window completed. Switching to silence detection.")
#                 self.in_fixed_window = False
#
#             # Keep recording during the fixed window
#             return None
#
#         # Standard silence-based detection after the fixed window
#         if not vad_confidence:
#             self.gap_counter += 1
#             logger.debug(f"Gap counter: {self.gap_counter}/{self.buffer_size}")
#             return self.gap_counter
#         else:
#             self.gap_counter = 0  # Reset silence counter if audio is present
#             return None
#
#     def enable_listening(self):
#         """Enable audio processing."""
#         logger.info("Enabling listening.")
#         self.listening_enabled = True
#
#     def disable_listening(self):
#         """Disable audio processing."""
#         logger.info("Disabling listening.")
#         self.listening_enabled = False
