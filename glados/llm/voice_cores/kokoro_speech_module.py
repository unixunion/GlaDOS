import threading

import numpy as np
from kokoro_onnx import Kokoro
from scipy.signal import resample

from glados.config import GladosConfig, SAMPLE_RATE
from glados.llm.voice_cores.speech_module import SpeechModule


class KokoroSpeechModule(SpeechModule):
    """Voice core using Kokoro ONNX TTS."""

    def __init__(self, tts: Kokoro, tts_queue,
                 speaking_lock: threading.Event = None,
                 config: GladosConfig = None):
        super().__init__(tts_queue=tts_queue, speaking_lock=speaking_lock, config=config)
        self._kokoro = tts
        self._voice = getattr(config, 'speaker_id', None) or "bf_isabella"
        self._target_rate = SAMPLE_RATE

    def _synthesize(self, text: str) -> np.ndarray:
        samples, sample_rate = self._kokoro.create(
            text, voice=self._voice, speed=1.0, lang="en-us"
        )
        # Resample to match system sample rate if needed
        if sample_rate != self._target_rate:
            num_samples = int(len(samples) * self._target_rate / sample_rate)
            samples = resample(samples, num_samples)
        return np.asarray(samples, dtype=np.float32)

    def _get_sample_rate(self) -> int:
        return self._target_rate
