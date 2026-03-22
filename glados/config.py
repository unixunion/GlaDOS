import os
from dataclasses import dataclass, field
from typing import Optional, List, Sequence

import yaml

from glados.llm.client_type import ClientType

VAD_MODEL = "silero_vad.onnx"
VOICE_MODEL = "glados.onnx"
PAUSE_TIME = 0.05  # Time to wait between processing loops
SAMPLE_RATE = 16000  # Sample rate for input stream
VAD_SIZE = 50  # Milliseconds of sample for Voice Activity Detection (VAD)
VAD_THRESHOLD = 0.9  # Threshold for VAD detection
BUFFER_SIZE = 1200  # Milliseconds of silence before finalizing speech (also pre-wake buffer size)
PAUSE_LIMIT = 500  # Milliseconds of pause allowed before processing
SIMILARITY_THRESHOLD = 2  # Threshold for wake word similarity
MIN_SENTENCE_LENGTH = 3

NEUROTOXIN_RELEASE_ALLOWED = False  # preparation for function calling, see issue #13
DEFAULT_PERSONALITY_PREPROMPT = (
    {
        "role": "system",
        "content": "You are a helpful AI assistant. You are here to assist the user in their tasks.",
    },
)


class WakeWordConfig:
    name: str
    sensitivity: float
    file: str


@dataclass
class PorcupineConfig:
    access_key: str = field(
        default_factory=lambda: os.environ.get("PORCUPINE_ACCESS_KEY") or
                                ValueError("Environment variable PORCUPINE_ACCESS_KEY is not set.")
    )
    wake_words: List[WakeWordConfig] = field(default_factory=list)


@dataclass
class PluginConfig:
    name: str
    config: dict


@dataclass
class GladosConfig:
    completion_url: str
    model: str
    api_key: Optional[str]
    wake_word: Optional[str]  # this was used for text based wakewords, should be deprecated
    announcement: Optional[str]
    personality_preprompt: List[dict[str, str]]
    wake_word_sensitivity: float
    client_type: ClientType.OPENAI
    interruptible: bool = False  # deprecated, use interrupt_on_wakeword instead
    interrupt_on_wakeword: bool = False  # if true, saying the wake word while GlaDOS is speaking stops TTS and switches to listening
    hardware_echo_cancellation: bool = False  # if the speaker features hardware echo_cancellation, we likely dont need to supress recording as much while speaking
    vision_enabled: bool = True
    vision_images_path: str = None
    vision_completion_url: str = None
    voice_model: str = VOICE_MODEL
    vision_model: str = None
    speaker_id: Optional[int] = None
    display_port: int = 5001
    thinking_enabled: bool = False  # Allow models to use [THINK] reasoning tags; disable for faster responses
    max_context_messages: int = 20  # Max messages per activity context; older messages are trimmed to keep context small and fast
    music_dir: Optional[str] = None  # Path to music directory for the music player plugin
    speech_buffer_ms: int = BUFFER_SIZE  # Milliseconds of silence before finalizing speech; also sets the pre-wake audio buffer size
    plugin_intent_threshold: float = 0.5
    plugins: List[PluginConfig] = field(default_factory=list)
    porcupine: PorcupineConfig = None
    openwakeword: Optional[dict] = None
    mcp_servers: Optional[List[dict]] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: str, key_to_config: Sequence[str] | None = ("Glados",)):
        key_to_config = key_to_config or []

        try:
            # First attempt with UTF-8
            with open(path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
        except UnicodeDecodeError:
            # Fall back to utf-8-sig if UTF-8 fails (handles BOM)
            with open(path, "r", encoding="utf-8-sig") as file:
                data = yaml.safe_load(file)

        config = data
        for nested_key in key_to_config:
            config = config[nested_key]

        return cls(**config)
