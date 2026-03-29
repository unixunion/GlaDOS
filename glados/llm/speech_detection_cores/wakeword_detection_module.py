import os
import threading

import pvporcupine
from loguru import logger
from pvrecorder import PvRecorder

from glados.config import GladosConfig, PorcupineConfig
from glados.system.event_system import EventMessage, EventSystem

event_system = EventSystem()


class WakeWordDetectionModule:
    def __init__(self,
                 audio_device_index=-1,
                 interrupt_event=None,
                 config: GladosConfig = None):
        """
        Initializes the Wake Word Detection Module using Porcupine.
        Args:
            keyword_file_paths (str): Path to the keyword ppn library.
            sensitivity (float): Detection sensitivity (0.0 to 1.0).
            access_key (str): Picovoice access key for Porcupine.
            audio_device_index (int): Index of the input audio device (-1 for default).
            interrupt_event (threading.Event): Shared event to signal interruptions.
        """

        porcupine_config: PorcupineConfig = config.porcupine

        # Transform the wake_words into Porcupine client arguments
        if porcupine_config and porcupine_config['wake_words']:
            keyword_file_paths = [word['file'] for word in porcupine_config['wake_words']]
            keywords = [word['name'] for word in porcupine_config['wake_words']]
            sensitivities = [word['sensitivity'] for word in porcupine_config['wake_words']]

            access_key = os.environ.get('PORCUPINE_ACCESS_KEY', None)
            if 'access_key' in porcupine_config and not access_key:
                access_key = porcupine_config['access_key']
            if not access_key:
                raise ValueError(
                    "Porcupine access key must be set either in the configuration or as an environment variable.")

            # Initialize the Porcupine client
            self.porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=keyword_file_paths,
                keywords=keywords,
                sensitivities=sensitivities,
            )
        else:
            raise ValueError("No wake words configured for Porcupine.")

        self.sample_rate = self.porcupine.sample_rate
        self.frame_length = self.porcupine.frame_length
        self.audio_device_index = audio_device_index
        self.interrupt_event = interrupt_event

        # self.queue = Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.recorder = None

        logger.info("WakeWordDetectionModule initialized.")
        logger.info(f"  Porcupine version: {self.porcupine.version}")
        logger.info(f"  Audio device index: {self.audio_device_index}")
        logger.info(f"  Frame length: {self.frame_length}")
        logger.info(f"  Sample rate: {self.sample_rate}")

    def start(self):
        """Start the wake word detection."""
        if self.thread.is_alive():
            logger.warning("WakeWordDetectionModule is already running.")
            return
        self.stop_event.clear()
        self.thread.start()
        logger.info("Wake word detection started.")

    def stop(self):
        """Stop the wake word detection."""
        self.stop_event.set()
        self.thread.join()
        if self.recorder:
            self.recorder.delete()
        self.porcupine.delete()
        logger.info("Wake word detection stopped.")

    def _run(self):
        """Run the wake word detection loop."""
        try:
            self.recorder = PvRecorder(frame_length=self.frame_length, device_index=self.audio_device_index)
            self.recorder.start()
            logger.info("Listening for wake word...")

            while not self.stop_event.is_set():
                pcm = self.recorder.read()
                result = self.porcupine.process(pcm)
                if result >= 0:
                    logger.success("Wake word detected!")
                    if self.interrupt_event:
                        self.interrupt_event.set()
                    # self.queue.put("wake_word_detected")
                    # Publish the wake word detection event
                    if event_system:
                        event_system.publish(EventMessage("system", "wake_word_detected", {}))
        except Exception as e:
            logger.error(f"Error in WakeWordDetectionModule: {e}")
        finally:
            if self.recorder:
                self.recorder.delete()
