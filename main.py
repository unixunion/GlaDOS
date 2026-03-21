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
load_plugins("plugins")


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
    def __init__(self, config_path: str, speech_enabled: bool = True):
        # Load configuration
        self.config = GladosConfig.from_yaml(config_path)
        self.speech_enabled = speech_enabled

        # Event system
        self.event_system = EventSystem()

        # Discover available models before creating clients
        discover_models(self.config)

        # Client for LLM interactions
        self.client = ChatClient(self.config)
        # Queue the startup announcement so it's processed by the LLM thread
        # alongside any plugin load events, avoiding duplicate responses
        self.client.llm_queue.put("You have just been powered on")

        if self.config.vision_enabled:
            self.vision_client = VisionClient(self.config)
        else:
            self.vision_client = None
            logger.info("Vision system disabled by configuration.")

        # a lock used to mask when the voice module is talking, so the assistant doesnt hear
        # itself, #TODO find a better fix
        self.speaking_lock = threading.Event()

        self.speech_module = None
        self.wakeword_module = None
        self.voice_detection = None
        self.wakeword_interrupt = threading.Event()

        if self.speech_enabled:
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

            self.wakeword_module = OpenWakeWordDetectionModule(
                interrupt_event=self.wakeword_interrupt,
                config=self.config,
                speaking_lock=self.speaking_lock,
            )

            self.voice_detection = WhisperVoiceDetectionModule(
                vad.VAD(model_path=str(Path.cwd() / "models" / VAD_MODEL)),
                client_queue=self.client.llm_queue,
                interrupt_event=self.wakeword_interrupt,
                whisper_model_size="small",
                speaking_lock=self.speaking_lock,
                wakeword_module=self.wakeword_module,
                buffer_size_ms=self.config.speech_buffer_ms,
            )
        else:
            logger.info("Speech disabled — text-only mode. No TTS, VAD, or wake word.")

        # Thread management
        self.shutdown_event = threading.Event()

    def start(self):
        """
        Start the GLaDOS assistant.
        """
        if self.wakeword_module:
            self.wakeword_module.start()
            logger.info("Wake word detection started.")

        if self.voice_detection:
            self.voice_detection.start()
            logger.info("Voice detection started.")

        if self.speech_module:
            self.speech_module.start()
            logger.info("Speech module started.")

    def stop(self):
        """
        Stop the GLaDOS assistant.
        """
        if self.voice_detection:
            self.voice_detection.stop()
            logger.info("Voice detection stopped.")

        if self.speech_module:
            self.speech_module.stop()
            logger.info("Speech module stopped.")

    def chat(self, message: str):
        """
        Sends a message to the client for processing.
        """
        self.client.chat(
            message,
            tools=plugin_manager.get_available_tools(),
        )


def _drain_tts_queue_to_console(tts_queue):
    """Background thread that prints TTS output to console instead of speaking."""
    while True:
        try:
            text = tts_queue.get(timeout=0.1)
            if text == "<EOS>":
                print()  # Blank line between responses
            else:
                print(f"  GlaDOS: {text}")
        except Exception:
            continue


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GlaDOS Assistant")
    parser.add_argument("--text", action="store_true", help="Text input mode (CLI chat, TTS output)")
    parser.add_argument("--no-speech", action="store_true",
                        help="Text-only mode: no TTS, no mic, no wake word. Pure text in/out for testing.")
    args = parser.parse_args()

    speech_enabled = not args.no_speech and not args.text

    # Initialize GLaDOS
    glados = Glados2(config_path="glados_config.yml", speech_enabled=speech_enabled or args.text)

    try:
        if args.no_speech:
            # Pure text mode: no audio hardware at all
            # Drain TTS queue to console in background
            drain_thread = threading.Thread(
                target=_drain_tts_queue_to_console, args=(glados.client.tts_queue,), daemon=True
            )
            drain_thread.start()
            logger.info("Text-only mode (no speech). Type your messages below.")
            print("GlaDOS text-only mode. Type 'exit' to quit.\n")
            while True:
                try:
                    user_input = input("You: ")
                except EOFError:
                    break
                if user_input.strip().lower() in ("exit", "quit"):
                    break
                if user_input.strip():
                    glados.chat(user_input)
        elif args.text:
            # CLI text mode: type to GlaDOS, she responds via TTS
            if glados.speech_module:
                glados.speech_module.start()
            logger.info("Text mode started. Type your messages below.")
            print("GlaDOS text mode (with TTS). Type 'exit' to quit.")
            while True:
                try:
                    user_input = input("You: ")
                except EOFError:
                    break
                if user_input.strip().lower() in ("exit", "quit"):
                    break
                if user_input.strip():
                    glados.chat(user_input)
        else:
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
