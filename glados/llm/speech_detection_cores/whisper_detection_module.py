import os
import queue
import threading
import time
import wave
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
        self._listen_timeout_seconds = 0
        self._listen_start_time = 0
        self._audio_callback_count = 0
        self._last_audio_health_log = 0

        self.muted = False  # When True, ignore wake words and drop all input except unmute commands

        # Load the "listening" confirmation beep
        self._listen_beep = None
        beep_path = os.path.join(os.getcwd(), "sounds", "listen_beep.wav")
        if os.path.exists(beep_path):
            with wave.open(beep_path, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                self._listen_beep = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
                self._listen_beep_rate = wf.getframerate()
            logger.info(f"Loaded listen beep: {beep_path}")
        else:
            logger.warning(f"Listen beep not found at {beep_path}")

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

    def _play_listen_beep(self):
        """Play a short confirmation tone to signal that recording has started."""
        if self._listen_beep is not None:
            try:
                sd.play(self._listen_beep, samplerate=self._listen_beep_rate, blocking=False)
            except Exception as e:
                logger.debug(f"Could not play listen beep: {e}")

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
                        self.interrupt_event.clear()
                        self._duck_music()  # Lower music volume so mic can hear user
                        if self.muted:
                            # Still need to listen briefly so we can transcribe an unmute command
                            logger.info("Muted — listening briefly for unmute command only.")
                            self._play_listen_beep()
                            self._listen_timeout_seconds = 8
                            self._listen_start_time = time.time()
                            self.enable_listening()
                        else:
                            logger.info("Interrupt event detected. Enabling listening.")
                            self._play_listen_beep()
                            self._listen_timeout_seconds = 0  # wake word listening has no timeout
                            self.enable_listening()

                    # Auto-disable listening after timeout if no speech detected
                    if (self.listening_enabled
                            and not self.recording_started
                            and self._listen_timeout_seconds > 0
                            and time.time() - self._listen_start_time > self._listen_timeout_seconds):
                        logger.info("Listen timeout expired, disabling listening. Say a wake word to re-activate.")
                        self.disable_listening()
                        self._unduck_music()
                        self._listen_timeout_seconds = 0

                    # Periodic audio health check (every 30s)
                    now = time.time()
                    if now - self._last_audio_health_log > 30:
                        logger.info(f"Audio health: {self._audio_callback_count} callbacks, "
                                    f"listening={self.listening_enabled}, "
                                    f"speaking_lock={self.speaking_lock.is_set()}")
                        self._audio_callback_count = 0
                        self._last_audio_health_log = now

                    time.sleep(0.1)  # Keep the thread alive
        except Exception as e:
            logger.error(f"Error in VoiceDetectionModule stream: {e}")
        finally:
            logger.info("Voice detection thread exited.")

    def _is_music_playing(self) -> bool:
        """Check if music is currently playing (to avoid transcribing lyrics)."""
        try:
            from plugins.music.music_player import MusicPlayer
            return MusicPlayer().is_playing
        except Exception:
            return False

    def _duck_music(self):
        """Lower Spotify volume so the mic can hear the user over music."""
        try:
            from plugins.music.music_player import MusicPlayer
            player = MusicPlayer()
            if player.sp and player.is_playing:
                pb = player.sp.current_playback()
                if pb:
                    self._pre_duck_volume = pb.get("device", {}).get("volume_percent", 50)
                    player.sp.volume(10)  # Duck to 10%
                    logger.info(f"Music ducked: {self._pre_duck_volume}% → 10%")
        except Exception as e:
            logger.debug(f"Could not duck music: {e}")

    def _unduck_music(self):
        """Restore Spotify volume after voice input is processed."""
        try:
            vol = getattr(self, "_pre_duck_volume", None)
            if vol is not None:
                from plugins.music.music_player import MusicPlayer
                player = MusicPlayer()
                if player.sp:
                    player.sp.volume(vol)
                    logger.info(f"Music unducked: → {vol}%")
                self._pre_duck_volume = None
        except Exception as e:
            logger.debug(f"Could not unduck music: {e}")

    def listen_for_response(self, event: EventMessage):
        """Auto-enable listening after TTS finishes so the user can respond without wake word."""
        if self.muted:
            logger.info("Muted — skipping listen_for_response.")
            return
        if self._is_music_playing():
            logger.info("Music playing — skipping listen_for_response to avoid transcribing lyrics.")
            return
        # Wait briefly for speaker audio to die down so we don't transcribe our own TTS
        time.sleep(0.5)
        self._play_listen_beep()
        # Reset VAD state but keep the pre-wake buffer — flushing it causes
        # the start of the user's speech to be clipped when VAD triggers
        self.vad_model.reset()
        logger.info("Enabling listening for follow-up response (no wake word needed).")
        self._listen_timeout_seconds = 10  # seconds to wait for user to start speaking
        self._listen_start_time = time.time()
        self.enable_listening()

    def _audio_callback(self, indata, frames, time, status):
        """Audio callback for processing audio chunks."""
        try:
            if status:
                logger.warning(f"Audio callback status: {status}")
            data = indata.copy().squeeze()
            self._audio_callback_count += 1

            # Forward audio to wake word module (always, even during speaking lock)
            if self.wakeword_module is not None:
                self.wakeword_module.push_audio(data.copy())

            # Skip processing if speaking lock is set
            if self.speaking_lock.is_set():
                return

            # Buffer pre-wake audio when not actively recording so
            # the lead-in audio is available when VAD triggers.
            if not self.listening_enabled:
                if self.pre_wake_buffer.full():
                    self.pre_wake_buffer.get()
                self.pre_wake_buffer.put(data)
                return

            # Process audio for VAD and activation
            vad_value = self.vad_model.process_chunk(data)
            vad_confidence = vad_value > self.vad_threshold

            logger.debug(f"VAD: {vad_value:.3f} (threshold: {self.vad_threshold})")

            with self.lock:
                if not self.recording_started:
                    # Keep rolling buffer while waiting for speech so
                    # we capture audio just before VAD fires.
                    if self.pre_wake_buffer.full():
                        self.pre_wake_buffer.get()
                    self.pre_wake_buffer.put(data)

                    if vad_confidence:
                        logger.info("Voice activity detected. Activating recording.")
                        self.event_system.publish(EventMessage("status", "listening", {"message": "Listening..."}))
                        # Pre-wake buffer already contains the current chunk
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

    # Phrases that mute/unmute the system (matched case-insensitively against transcription)
    _MUTE_PHRASES = ["stop listening", "go to sleep", "mute yourself", "be quiet"]
    _UNMUTE_PHRASES = ["start listening", "resume listening", "wake up", "unmute"]

    # Phrases that dismiss an active alarm or stop music
    _STOP_PHRASES = ["stop", "cancel", "silence", "quiet", "shut up", "dismiss", "snooze", "enough"]

    def _check_mute_command(self, text: str) -> bool:
        """Check if text contains a mute/unmute command. Returns True if the text was a command (should not be forwarded)."""
        lower = text.strip().lower()
        for phrase in self._UNMUTE_PHRASES:
            if phrase in lower:
                if self.muted:
                    self.muted = False
                    logger.success("Unmuted — resuming normal operation.")
                    self.event_system.publish(EventMessage("status", "listening", {"message": "Listening resumed"}))
                return True
        for phrase in self._MUTE_PHRASES:
            if phrase in lower:
                if not self.muted:
                    self.muted = True
                    logger.success("Muted — ignoring all input until unmute command.")
                    self.event_system.publish(EventMessage("status", "idle", {"message": "Stopped listening"}))
                return True
        return False

    def _check_stop_command(self, text: str) -> bool:
        """Check if text is a stop command targeting an active alarm or music.

        Priority: ringing alarm > playing music. Returns True if handled.
        """
        lower = text.strip().lower()
        is_stop = any(phrase in lower for phrase in self._STOP_PHRASES)
        if not is_stop:
            return False

        # Priority 1: dismiss a ringing alarm
        try:
            from plugins.basic.alarm_clock import AlarmClock
            alarm = AlarmClock()
            if alarm.ringing:
                alarm.dismiss()
                logger.success(f"Alarm dismissed via voice command: '{text}'")
                self.event_system.publish(EventMessage("status", "idle", {"message": "Alarm dismissed"}))
                return True
        except Exception as e:
            logger.debug(f"Alarm check failed: {e}")

        # Priority 2: stop playing music
        try:
            from plugins.music.music_player import MusicPlayer
            player = MusicPlayer()
            if player.is_playing:
                player.play_music(action="STOP")
                logger.success(f"Music stopped via voice command: '{text}'")
                self.event_system.publish(EventMessage("status", "idle", {"message": "Music stopped"}))
                return True
        except Exception as e:
            logger.debug(f"Music check failed: {e}")

        # Not a stop command for any active target — let it through to the LLM
        return False

    def _process_detected_audio(self):
        """Process detected audio and send to LLM client."""
        try:
            # Restore music volume now that we've captured the audio
            self._unduck_music()
            audio = np.concatenate(self.samples)
            audio_duration_sec = len(audio) / self.sample_rate
            logger.info(f"Transcribing {audio_duration_sec:.1f}s of audio using Whisper...")
            audio_normalized = audio / np.max(np.abs(audio))
            detected_text = self.asr_model.transcribe(audio_normalized, fp16=False, language="en")["text"]

            logger.info(f"Transcribed text: {detected_text}")

            if not detected_text:
                logger.warning("No text detected from audio.")
                return

            # Check for mute/unmute commands first
            if self._check_mute_command(detected_text):
                return

            # If muted, drop the input
            if self.muted:
                logger.info(f"Muted — dropping input: {detected_text}")
                return

            # Intercept stop/cancel commands for active alarms or music
            if self._check_stop_command(detected_text):
                return

            logger.info(f"Queuing transcription for LLM (queue size: {self.client_queue.qsize()})")
            self.client_queue.put(detected_text)
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

