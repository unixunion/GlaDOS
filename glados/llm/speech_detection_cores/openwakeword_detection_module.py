import threading

import numpy as np
import sounddevice as sd
from loguru import logger
from openwakeword.model import Model as OwwModel

from glados.config import GladosConfig
from glados.system.event_system import EventMessage, EventSystem

event_system = EventSystem()

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80ms at 16kHz, recommended by openwakeword


class OpenWakeWordDetectionModule:
    def __init__(self,
                 audio_device_index=None,
                 interrupt_event=None,
                 config: GladosConfig = None):
        self.interrupt_event = interrupt_event
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.audio_device_index = audio_device_index

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

    def _run(self):
        try:
            logger.info("Listening for wake word (OpenWakeWord)...")

            while not self.stop_event.is_set():
                try:
                    # Use a fresh, exclusive stream per read to avoid conflicts
                    with sd.InputStream(
                        samplerate=SAMPLE_RATE,
                        channels=1,
                        dtype="int16",
                        blocksize=FRAME_SAMPLES,
                        device=self.audio_device_index,
                    ) as stream:
                        while not self.stop_event.is_set():
                            audio_data, overflowed = stream.read(FRAME_SAMPLES)
                            if overflowed:
                                logger.warning("Audio buffer overflow in wake word detection")
                            audio_int16 = audio_data[:, 0]
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
                except sd.PortAudioError as e:
                    logger.error(f"Audio device error: {e}, retrying in 1s...")
                    self.stop_event.wait(timeout=1.0)

        except Exception as e:
            logger.error(f"Error in OpenWakeWordDetectionModule: {e}")
