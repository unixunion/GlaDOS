import threading

import numpy as np

from glados.config import GladosConfig
from glados.llm.voice_cores.speech_module import SpeechModule


class GladosSpeechModule(SpeechModule):
    """Voice core using Piper/ONNX TTS (the classic GlaDOS voice)."""

    def __init__(self, tts, tts_queue,
                 interruptible: bool = True,
                 speaking_lock: threading.Event = None,
                 config: GladosConfig = None):
        super().__init__(tts_queue=tts_queue, speaking_lock=speaking_lock, config=config)
        self._tts = tts

    def _synthesize(self, text: str) -> np.ndarray:
        return self._tts.generate_speech_audio(text)

    def _get_sample_rate(self) -> int:
        return self._tts.rate
