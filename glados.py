import queue
import threading
import sys
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple, List

import numpy as np
import sounddevice as sd
from sounddevice import CallbackFlags
from Levenshtein import distance
from loguru import logger

from glados import asr, tts, vad
from glados.config import GladosConfig, DEFAULT_PERSONALITY_PREPROMPT, VAD_SIZE, VAD_MODEL, VAD_THRESHOLD, \
    SAMPLE_RATE
from glados.llmclient import LLMClient
from glados.util import replace_numbers_with_words
from plugins.plugin_manager import PluginManager, load_plugins

logger.remove(0)
logger.add(sys.stderr, level="INFO")


class Glados:
    def __init__(self, voice_model: str, speaker_id: Optional[int], completion_url: str, model: str,
                 api_key: Optional[str] = None, wake_word: Optional[str] = None,
                 personality_preprompt: Sequence[dict[str, str]] = DEFAULT_PERSONALITY_PREPROMPT,
                 announcement: Optional[str] = None, interruptible: bool = True) -> None:
        """
        Initializes the Glados assistant with models, plugins, and configuration.
        """
        self.completion_url = completion_url
        self.model = model
        self.wake_word = wake_word
        self._vad_model = vad.VAD(model_path=str(Path.cwd() / "models" / VAD_MODEL))
        self._asr_model = asr.AudioTranscriber()
        self._tts = tts.Synthesizer(
            model_path=str(Path.cwd() / "models" / voice_model),
            speaker_id=speaker_id,
        )

        # Warm up ASR model
        self._asr_model.transcribe_file("data/0.wav")

        # Initialize queues
        self._sample_queue = queue.Queue()

        # Circular buffer for pre-activation audio
        self._buffer = queue.Queue(maxsize=600 // VAD_SIZE)
        self._recording_started = False
        self._gap_counter = 0
        self.processing = False
        self.interruptible = interruptible
        self.shutdown_event = threading.Event()

        # Add a flag for TTS playback
        self.currently_playing = False

        # Plugin and LLM setup
        self.plugin_manager = PluginManager()
        load_plugins("plugins")
        self.llm_client = LLMClient(url=completion_url, model=model, headers={
            "Authorization": f"Bearer {api_key or 'your_api_key_here'}",
            "Content-Type": "application/json"
        }, config=config)

        # Threads for LLM and TTS processing
        threading.Thread(target=self.process_llm, daemon=True).start()
        threading.Thread(target=self.process_tts, daemon=True).start()

        if announcement:
            audio = self._tts.generate_speech_audio(announcement)
            logger.success(f"TTS text: {announcement}")
            sd.play(audio, self._tts.rate)
            if not self.interruptible:
                sd.wait()

        def audio_callback(indata: np.ndarray, frames: int, time: Any, status: CallbackFlags):
            data = indata.copy().squeeze()  # Reduce to single channel if necessary
            vad_value = self._vad_model.process_chunk(data)
            vad_confidence = vad_value > VAD_THRESHOLD
            self._sample_queue.put((data, vad_confidence))

        self.input_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=audio_callback,
            blocksize=int(SAMPLE_RATE * VAD_SIZE / 1000),
        )

    def start(self):
        """
        Starts the Glados assistant.
        """
        self.input_stream.start()
        logger.success("Input Stream Started")
        logger.info("Listening...")
        try:
            while not self.shutdown_event.is_set():
                sample, vad_confidence = self._sample_queue.get()
                self._handle_audio_sample(sample, vad_confidence)
        except KeyboardInterrupt:
            self.shutdown()

    def _handle_audio_sample(self, sample: np.ndarray, vad_confidence: bool):
        if not self._recording_started:
            self._manage_pre_activation_buffer(sample, vad_confidence)
        else:
            self._process_activated_audio(sample, vad_confidence)

    def _manage_pre_activation_buffer(self, sample: np.ndarray, vad_confidence: bool):
        if self._buffer.full():
            self._buffer.get()
        self._buffer.put(sample)
        if vad_confidence:
            self._recording_started = True
            self.processing = False
            self._samples = list(self._buffer.queue)

    def _process_activated_audio(self, sample: np.ndarray, vad_confidence: bool):
        if self.currently_playing:
            logger.debug("TTS playback in progress; skipping audio capture.")
            return

        self._samples.append(sample)
        if not vad_confidence:
            self._gap_counter += 1
            if self._gap_counter >= 500 // VAD_SIZE:
                self._process_detected_audio()
        else:
            self._gap_counter = 0

    def _process_detected_audio(self):

        if self.currently_playing:
            logger.debug("Skipping audio processing as TTS is currently playing.")
            self.reset()
            return

        audio_data = np.concatenate(self._samples)
        detected_text = self._asr_model.transcribe(audio_data)

        if detected_text:
            logger.success(f"ASR text: {detected_text}")
            if not self.wake_word or self._wakeword_detected(detected_text):
                self.llm_client.llm_queue.put(detected_text)
        self.reset()

    def reset(self):
        self._recording_started = False
        self._samples = []
        self._gap_counter = 0
        with self._buffer.mutex:
            self._buffer.queue.clear()

    def _wakeword_detected(self, text: str) -> bool:
        words = text.split()
        return any(distance(word.lower(), self.wake_word.lower()) < 2 for word in words)

    # def process_llm(self):
    #     while not self.shutdown_event.is_set():
    #         try:
    #             detected_text = self.llm_client.llm_queue.get(timeout=0.1)
    #             response = self.llm_client.chat(detected_text)
    #             self.llm_client.llm_queue.put(response)
    #         except queue.Empty:
    #             continue

    def process_llm(self):
        """
        Processes the detected text using the LLM.
        """
        while not self.shutdown_event.is_set():
            try:
                # Get user input from the LLM queue
                detected_text = self.llm_client.llm_queue.get(timeout=0.1)
                self.llm_client.chat(detected_text)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in process_llm: {e}")

    def process_tts(self):
        """
        Processes the LLM-generated text using the TTS model.
        Handles playback, interruptions, and <EOS> tokens.
        """
        assistant_text = []  # Text generated by the assistant for TTS
        system_text = []  # Text logged when TTS is interrupted
        finished = False  # Indicates if TTS has finished speaking
        interrupted = False  # Indicates if TTS playback was interrupted
        self.currently_playing = False  # Playback state flag

        while not self.shutdown_event.is_set():
            try:
                text = self.llm_client.tts_queue.get(timeout=0.1)

                if text == "<EOS>":  # Handle end-of-stream token
                    finished = True
                elif not text:
                    logger.warning("Empty string sent to TTS.")  # Log if empty text
                else:
                    logger.info(f"TTS input text: {text}")
                    self.currently_playing = True  # Start playback

                    # Generate audio from TTS
                    adjusted_text = replace_numbers_with_words(text)
                    audio = self._tts.generate_speech_audio(adjusted_text)
                    total_samples = len(audio)
                    if total_samples:
                        sd.play(audio, self._tts.rate)

                        # Track playback and detect interruptions
                        interrupted, percentage_played = self.percentage_played(
                            total_samples
                        )
                        if interrupted:
                            clipped_text = self.clip_interrupted_sentence(
                                text, percentage_played
                            )
                            logger.info(
                                f"TTS interrupted at {percentage_played:.2f}%: {clipped_text}"
                            )
                            system_text = assistant_text[:]
                            system_text.append(clipped_text)
                            finished = True

                        assistant_text.append(text)
                        sd.wait()  # Ensure playback completes or interruption is handled

                    self.currently_playing = False  # End playback

                if finished:
                    # Append the completed assistant message
                    self.llm_client.messages.append(
                        {"role": "assistant", "content": " ".join(assistant_text)}
                    )
                    # Optionally log the interrupted text
                    if interrupted:
                        self.llm_client.messages.append(
                            {
                                "role": "system",
                                "content": f"USER INTERRUPTED GLADOS, TEXT DELIVERED: {' '.join(system_text)}",
                            }
                        )
                    # Reset flags and buffers
                    assistant_text.clear()
                    finished = False
                    interrupted = False

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in process_tts: {e}")
                self.currently_playing = False

    def percentage_played(self, total_samples: int) -> Tuple[bool, float]:
        """
        Tracks the playback progress and detects interruptions.

        Args:
            total_samples (int): Total number of samples in the audio.

        Returns:
            Tuple[bool, float]: (Whether playback was interrupted, Percentage of playback completed).
        """
        elapsed_samples = 0
        while sd.get_stream().active and elapsed_samples < total_samples:
            elapsed_samples = sd.get_stream().time * self._tts.rate
            if self.shutdown_event.is_set():
                return True, (elapsed_samples / total_samples) * 100
        return False, 100.0

    def clip_interrupted_sentence(self, text: str, percentage: float) -> str:
        """
        Clips the sentence based on the percentage of playback completed.

        Args:
            text (str): The original text.
            percentage (float): Percentage of the sentence that was played.

        Returns:
            str: The truncated text.
        """
        words = text.split()
        clip_index = int(len(words) * (percentage / 100))
        return " ".join(words[:clip_index]) + "..."

    def shutdown(self):
        self.shutdown_event.set()
        self.input_stream.stop()
        logger.success("Shutting down Glados.")


if __name__ == "__main__":
    config = GladosConfig.from_yaml("glados_config.yml")
    assistant = Glados(
        voice_model=config.voice_model,
        speaker_id=config.speaker_id,
        completion_url=config.completion_url,
        model=config.model,
        api_key=config.api_key,
        wake_word=config.wake_word,
        personality_preprompt=config.personality_preprompt,
        announcement=config.announcement,
        interruptible=config.interruptible,
    )
    assistant.start()


