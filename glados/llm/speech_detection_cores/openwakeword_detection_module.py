import queue
import threading

import numpy as np
from loguru import logger
from openwakeword.model import Model as OwwModel

from glados.config import GladosConfig
from glados.system.event_system import EventMessage, EventSystem

event_system = EventSystem()

FRAME_SAMPLES = 1280  # 80ms at 16kHz, recommended by openwakeword


class OpenWakeWordDetectionModule:
    def __init__(self,
                 interrupt_event=None,
                 config: GladosConfig = None,
                 speaking_lock: threading.Event = None):
        self.interrupt_event = interrupt_event
        self.speaking_lock = speaking_lock
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.audio_queue = queue.Queue(maxsize=100)

        oww_config = config.openwakeword if config else {}
        self.threshold = oww_config.get("threshold", 0.5)

        model_paths = oww_config.get("models", [])
        if model_paths:
            self.oww_model = OwwModel(wakeword_models=model_paths, inference_framework="onnx")
        else:
            self.oww_model = OwwModel(inference_framework="onnx")

        # Get the actual prediction keys by doing a dummy prediction
        dummy = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        dummy_result = self.oww_model.predict(dummy)
        self.model_names = list(dummy_result.keys())
        self.oww_model.reset()

        logger.info(f"OpenWakeWord initialized with models: {self.model_names}")
        logger.info(f"Detection threshold: {self.threshold}")

    def start(self):
        if self.thread.is_alive():
            logger.warning("OpenWakeWordDetectionModule is already running.")
            return
        self.stop_event.clear()
        self.thread.start()
        logger.info("OpenWakeWord detection started.")

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        logger.info("OpenWakeWord detection stopped.")

    def push_audio(self, audio_data: np.ndarray):
        """Push audio data from a shared input stream for wake word processing."""
        try:
            self.audio_queue.put_nowait(audio_data)
        except queue.Full:
            pass  # Drop frames if we can't keep up

    def _run(self):
        try:
            logger.info("Listening for wake word (OpenWakeWord)...")

            while not self.stop_event.is_set():
                # Skip processing while TTS is playing
                if self.speaking_lock and self.speaking_lock.is_set():
                    self.oww_model.reset()
                    # Drain any queued audio
                    while not self.audio_queue.empty():
                        try:
                            self.audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    self.stop_event.wait(timeout=0.1)
                    continue

                try:
                    audio_data = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Convert float32 audio to int16 for openwakeword
                if audio_data.dtype != np.int16:
                    audio_int16 = (audio_data * 32767).astype(np.int16)
                else:
                    audio_int16 = audio_data

                # Flatten if needed
                if audio_int16.ndim > 1:
                    audio_int16 = audio_int16[:, 0]

                prediction = self.oww_model.predict(audio_int16)

                for model_name in self.model_names:
                    score = prediction[model_name]
                    if score >= self.threshold:
                        logger.success(f"Wake word '{model_name}' detected! (score: {score:.3f})")
                        self.oww_model.reset()
                        if self.interrupt_event:
                            self.interrupt_event.set()
                        if event_system:
                            event_system.publish(EventMessage("system", "wake_word_detected", {}))

        except Exception as e:
            logger.error(f"Error in OpenWakeWordDetectionModule: {e}")
