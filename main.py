import logging
import os
import sys
import threading
import time
from pathlib import Path

from kokoro_onnx import Kokoro
from loguru import logger

from glados.llm.cores.chat_client import ChatClient
from glados.llm.cores.vision_client import VisionClient
from glados.llm.speech_detection_cores.wakeword_detection_module import WakeWordDetectionModule
from glados.llm.speech_detection_cores.whisper_detection_module import WhisperVoiceDetectionModule
from glados.llm.voice_cores.alternative_speech_module import AlternativeSpeechModule

# need to configure the logger before importing all the modules
logger.remove()
logger.add(sys.stderr, level="INFO")

from glados import tts, vad
from glados.config import GladosConfig, VAD_MODEL
from glados.llm.voice_cores.glados_speech_module import GladosSpeechModule
from plugins.event_system.event_system import EventSystem
from plugins.plugin_system.plugin_manager import PluginManager, load_plugins

plugin_manager = PluginManager()
load_plugins("plugins")


class Glados2:
    def __init__(self, config_path: str):
        # Load configuration
        self.config = GladosConfig.from_yaml(config_path)

        # Event system
        self.event_system = EventSystem()

        # Client for LLM interactions
        self.client = ChatClient(self.config)
        self.vision_client = VisionClient(self.config)

        # a lock used to mask when the voice module is talking, so the assistant doesnt hear
        # itself, #TODO find a better fix
        self.speaking_lock = threading.Event()

        # GlaDOS voice
        self.tts_synthesizer = tts.Synthesizer(
            model_path=str(Path("models") / self.config.voice_model),
            speaker_id=self.config.speaker_id,
        )
        self.speech_module = GladosSpeechModule(
            tts=self.tts_synthesizer,
            tts_queue=self.client.tts_queue,
            interruptible=self.config.interruptible,
            speaking_lock=self.speaking_lock
        )



        # Alternative Voice system
        # self.kokoro = Kokoro(
        #     "models/kokoro-82m-onnx/kokoro-v0_19.onnx",
        #     voices_path="models/kokoro-82m-onnx/voices.json"
        # )
        #
        # self.speech_module = AlternativeSpeechModule(
        #     tts=self.kokoro,
        #     tts_queue=self.client.tts_queue,
        #     interruptible=self.config.interruptible,
        #     speaking_lock=self.speaking_lock
        # )

        # Voice Detection Module
        # self.voice_detection = AsrVoiceDetectionModule(
        #     vad_model=vad.VAD(model_path=str(Path.cwd() / "models" / VAD_MODEL)),
        #     asr_model=asr.AudioTranscriber(),
        #     client_queue=self.client.llm_queue,
        #     wake_word=self.config.wake_word,
        #     sample_rate=SAMPLE_RATE,
        #     vad_chunk_size_ms=VAD_SIZE,
        #     buffer_size_ms=BUFFER_SIZE,
        #     vad_threshold=VAD_THRESHOLD,
        #     similarity_threshold=SIMILARITY_THRESHOLD,
        # )

        self.wakeword_interrupt = threading.Event()

        self.wakeword_module = WakeWordDetectionModule(
            keyword_file_paths=["glados/llm/speech_detection_cores/wakeword/Glad-os_en_windows_v3_0_0.ppn",
                                "glados/llm/speech_detection_cores/wakeword/gladys_en_windows_v3_0_0.ppn"],
            sensitivity=self.config.wake_word_sensitivity,
            access_key=os.environ['PORCUPINE_ACCESS_KEY'],
            interrupt_event=self.wakeword_interrupt,
        )

        self.voice_detection = WhisperVoiceDetectionModule(
            vad.VAD(model_path=str(Path.cwd() / "models" / VAD_MODEL)),
            client_queue=self.client.llm_queue,
            interrupt_event=self.wakeword_interrupt,
            whisper_model_size="base",
            speaking_lock=self.speaking_lock
        )

        # Thread management
        self.shutdown_event = threading.Event()

    def start(self):
        """
        Start the GLaDOS assistant.
        """
        # Start the wakeword detector
        self.wakeword_module.start()
        logger.info("Wake word detection started.")

        # Start the Voice Detection Module
        self.voice_detection.start()
        logger.info("Voice detection started.")

        # Start the Speech Module
        self.speech_module.start()
        logger.info("Speech module started.")

    def stop(self):
        """
        Stop the GLaDOS assistant.
        """
        # Stop the Voice Detection Module
        self.voice_detection.stop()
        logger.info("Voice detection stopped.")

        # Stop the Speech Module
        self.speech_module.stop()
        logging.info("Speech module stopped.")

    def chat(self, message: str):
        """
        Sends a message to the client for processing.
        """
        self.client.chat(
            message,
            tools=plugin_manager.get_available_tools(),
        )


if __name__ == "__main__":
    # Initialize GLaDOS
    glados = Glados2(config_path="glados_config.yml")

    try:
        glados.start()

        # Main loop to monitor wakeword detection and handle interactions
        while True:
            if glados.wakeword_interrupt.wait(timeout=5):
                logger.debug("wake word detected in main")
            else:
                logger.debug("wake word timed out")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutting down GLaDOS...")

    finally:
        glados.stop()
