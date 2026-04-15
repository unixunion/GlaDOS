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

def _log_colorizer(record):
    """Colorize log messages by subsystem based on module path."""
    name = record["name"]
    _ts = "<cyan>{time:HH:mm:ss.SSS}</cyan>"

    # Speech / TTS / voice detection — green
    if "speech_detection" in name or "whisper" in name or "wakeword" in name or "openwakeword" in name:
        return f"{_ts} | <green>{{level:<8}}</green> | <green>{{name}}:{{function}}:{{line}}</green> - {{message}}\n{{exception}}"
    if "voice_core" in name or "speech_module" in name or "kokoro" in name:
        return f"{_ts} | <light-green>{{level:<8}}</light-green> | <light-green>{{name}}:{{function}}:{{line}}</light-green> - {{message}}\n{{exception}}"

    # LLM core — yellow
    if "chat_client" in name or "queue_processor" in name or "event_handler" in name:
        return f"{_ts} | <yellow>{{level:<8}}</yellow> | <yellow>{{name}}:{{function}}:{{line}}</yellow> - {{message}}\n{{exception}}"
    if "stream_handler" in name or "response_processor" in name or "message_manager" in name:
        return f"{_ts} | <light-yellow>{{level:<8}}</light-yellow> | <light-yellow>{{name}}:{{function}}:{{line}}</light-yellow> - {{message}}\n{{exception}}"
    if "backend" in name or "tool_executor" in name:
        return f"{_ts} | <yellow>{{level:<8}}</yellow> | <yellow>{{name}}:{{function}}:{{line}}</yellow> - {{message}}\n{{exception}}"

    # NLP / intent classification — magenta
    if "nlp" in name or "dispatcher" in name or "intent_classifier" in name:
        return f"{_ts} | <magenta>{{level:<8}}</magenta> | <magenta>{{name}}:{{function}}:{{line}}</magenta> - {{message}}\n{{exception}}"

    # Knowledge / RAG — light blue
    if "knowledge" in name or "rag" in name or "conversation_rag" in name:
        return f"{_ts} | <light-blue>{{level:<8}}</light-blue> | <light-blue>{{name}}:{{function}}:{{line}}</light-blue> - {{message}}\n{{exception}}"

    # Memory — light cyan
    if "memory" in name:
        return f"{_ts} | <light-cyan>{{level:<8}}</light-cyan> | <light-cyan>{{name}}:{{function}}:{{line}}</light-cyan> - {{message}}\n{{exception}}"

    # Display / UI — white
    if "display" in name:
        return f"{_ts} | <white>{{level:<8}}</white> | <white>{{name}}:{{function}}:{{line}}</white> - {{message}}\n{{exception}}"

    # Music — light magenta
    if "music" in name or "spotify" in name:
        return f"{_ts} | <light-magenta>{{level:<8}}</light-magenta> | <light-magenta>{{name}}:{{function}}:{{line}}</light-magenta> - {{message}}\n{{exception}}"

    # Pantry / shopping — cyan
    if "pantry" in name or "shopping" in name:
        return f"{_ts} | <cyan>{{level:<8}}</cyan> | <cyan>{{name}}:{{function}}:{{line}}</cyan> - {{message}}\n{{exception}}"

    # Recipes — light red (orange-ish)
    if "recipe" in name:
        return f"{_ts} | <light-red>{{level:<8}}</light-red> | <light-red>{{name}}:{{function}}:{{line}}</light-red> - {{message}}\n{{exception}}"

    # Other plugins (timer, alarm, chores, vision, personality, etc.) — blue
    if "plugin" in name or "cores" in name:
        return f"{_ts} | <blue>{{level:<8}}</blue> | <blue>{{name}}:{{function}}:{{line}}</blue> - {{message}}\n{{exception}}"

    # System / event system — dim
    if "event_system" in name or "system" in name:
        return f"{_ts} | {{level:<8}} | {{name}}:{{function}}:{{line}} - {{message}}\n{{exception}}"

    # Default
    return f"{_ts} | {{level:<8}} | {{name}}:{{function}}:{{line}} - {{message}}\n{{exception}}"

logger.add(sys.stderr, level="INFO", format=_log_colorizer)

# Ring buffer sink — captures all logs for the LogAnalyzer plugin
from plugins.system.log_analyzer import LogRingBuffer, set_shared_buffer
_log_buffer = LogRingBuffer(maxlen=5000)
logger.add(_log_buffer.sink, level="DEBUG")
set_shared_buffer(_log_buffer)

# Suppress noisy warnings from HuggingFace/sentence-transformers/ChromaDB embedding model
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from glados import tts, vad
from glados.config import GladosConfig, VAD_MODEL
from glados.llm.voice_cores.speech_module import SpeechModule
from glados.system.event_system import EventSystem
from glados.system.plugin import PluginSystem, load_plugins

plugin_manager = PluginSystem()


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

        # Discover available models before creating clients (skip in NLP mode)
        if not getattr(self.config, 'nlp_mode', False):
            discover_models(self.config)

        # Client for LLM interactions
        self.client = ChatClient(self.config)

        # Load plugins after ChatClient so tts.speak subscriber is ready
        load_plugins("plugins")
        self.client.load_plugin_prompts()

        # Queue the NLP mode startup announcement (immediate, no LLM needed)
        if getattr(self.config, 'nlp_mode', False):
            self.client.tts_queue.put("System online. AI Core disabled.")
            self.client.tts_queue.put("<EOS>")

        if self.config.vision_enabled:
            self.vision_client = VisionClient(self.config)
        else:
            self.vision_client = None
            logger.info("Vision system disabled by configuration.")

        # a lock used to mask when the voice module is talking, so the assistant doesnt hear
        # itself, #TODO find a better fix
        self.speaking_lock = threading.Event()
        # Expose the lock so secondary voice cores (e.g. ebook reader narrator)
        # can share it — without this, a second SpeechModule would let the mic
        # activate while it's speaking and wake-word interrupts would bypass it.
        from glados.system.tts_runtime import TTSRuntime
        TTSRuntime().set_speaking_lock(self.speaking_lock)

        self.speech_module = None
        self.wakeword_module = None
        self.voice_detection = None
        self.wakeword_interrupt = threading.Event()

        if self.speech_enabled:
            self.speech_module = self._create_speech_module()

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

    def _create_speech_module(self) -> SpeechModule:
        """Create the appropriate speech module based on config."""
        if self.config.voice_core == "kokoro":
            from kokoro_onnx import Kokoro
            from glados.llm.voice_cores.kokoro_speech_module import KokoroSpeechModule
            model_file = Path("models") / self.config.voice_model
            if model_file.is_dir():
                # voice_model is a directory — find model and voices files inside
                voices_path = str(model_file / "voices-v1.0.bin")
                model_file = model_file / "kokoro-v0_19.onnx"
            else:
                # voice_model is an .onnx file — voices bin is in same directory
                voices_path = str(model_file.parent / "voices-v1.0.bin")
            kokoro = Kokoro(str(model_file), voices_path)
            logger.info(f"Using Kokoro voice core (model: {self.config.voice_model})")
            return KokoroSpeechModule(
                tts=kokoro,
                tts_queue=self.client.tts_queue,
                speaking_lock=self.speaking_lock,
                config=self.config,
            )
        else:
            from glados.llm.voice_cores.glados_speech_module import GladosSpeechModule
            # Piper expects speaker_id as int or None
            sid = self.config.speaker_id
            piper_speaker_id = int(sid) if sid is not None and str(sid).isdigit() else None
            synthesizer = tts.Synthesizer(
                model_path=str(Path("models") / self.config.voice_model),
                speaker_id=piper_speaker_id,
            )
            logger.info(f"Using GlaDOS/Piper voice core (model: {self.config.voice_model})")
            return GladosSpeechModule(
                tts=synthesizer,
                tts_queue=self.client.tts_queue,
                speaking_lock=self.speaking_lock,
                config=self.config,
            )

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

        self._send_power_on_prompt()

    def _send_power_on_prompt(self):
        """Send the power-on prompt to the LLM for a warm-up greeting.
        Only fires in LLM mode when power_on_prompt is configured."""
        if getattr(self.config, 'nlp_mode', False):
            return
        power_on = getattr(self.config, 'power_on_prompt', None)
        if power_on:
            logger.info(f"Sending power-on prompt to LLM: {power_on}")
            self.client.llm_queue.put((power_on, {"system_injected": True}))

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
            glados._send_power_on_prompt()
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
            glados._send_power_on_prompt()
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
