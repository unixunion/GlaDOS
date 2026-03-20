import logging
import os
import sys
import threading
import time
from pathlib import Path

import requests
from loguru import logger

from glados.llm.cores.chat_client import ChatClient
from glados.llm.cores.vision_client import VisionClient
from glados.llm.speech_detection_cores.openwakeword_detection_module import OpenWakeWordDetectionModule
from glados.llm.speech_detection_cores.whisper_detection_module import WhisperVoiceDetectionModule

# need to configure the logger before importing all the modules
logger.remove()
logger.add(sys.stderr, level="INFO")

from glados import tts, vad
from glados.config import GladosConfig, VAD_MODEL
from glados.llm.voice_cores.glados_speech_module import GladosSpeechModule
from glados.system.event_system import EventSystem
from glados.system.plugin import PluginSystem, load_plugins

plugin_manager = PluginSystem()
load_plugins("plugin_test")


def discover_models(config: GladosConfig):
    """Connect to configured endpoints and list available models."""
    endpoints = {
        "completion": config.completion_url,
        "vision": config.vision_completion_url,
    }

    for name, base_url in endpoints.items():
        if not base_url:
            logger.warning(f"No {name} URL configured, skipping model discovery.")
            continue

        # Strip trailing /v1 or similar to build the models endpoint
        url = base_url.rstrip("/")
        if not url.endswith("/models"):
            url = f"{url}/models"

        logger.info(f"Discovering {name} models at {url}...")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            if not models:
                logger.warning(f"No models found at {name} endpoint ({url}).")
                continue

            logger.success(f"Available {name} models:")
            for m in models:
                model_id = m.get("id", "unknown")
                logger.info(f"  - {model_id}")
        except requests.ConnectionError:
            logger.error(f"Could not connect to {name} endpoint at {url}. Is the server running?")
        except requests.Timeout:
            logger.error(f"Timeout connecting to {name} endpoint at {url}.")
        except Exception as e:
            logger.error(f"Error discovering {name} models: {e}")


class Glados2:
    def __init__(self, config_path: str):
        # Load configuration
        self.config = GladosConfig.from_yaml(config_path)

        # Event system
        self.event_system = EventSystem()

        # Discover available models before creating clients
        discover_models(self.config)

        # Client for LLM interactions
        self.client = ChatClient(self.config)
        try:
            self.client.chat("What is the time?")
        except Exception as e:
            logger.exception("Something bad with the test prompt.")

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
            speaking_lock=self.speaking_lock,
            config=self.config
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

        self.wakeword_module = OpenWakeWordDetectionModule(
            interrupt_event=self.wakeword_interrupt,
            config=self.config,
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
