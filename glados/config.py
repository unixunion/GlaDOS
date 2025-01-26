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
BUFFER_SIZE = 400  # Milliseconds of buffer before VAD detection
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
    interruptible: bool  # this should mean that if I say wakeword, the tts should stop speaking and listen for more
    client_type: ClientType.OPENAI
    hardware_echo_cancellation: bool = False  # if the speaker features hardware echo_cancellation, we likely dont need to supress recording as much while speaking
    vision_images_path: str = None
    vision_completion_url: str = None
    voice_model: str = VOICE_MODEL
    vision_model: str = None
    speaker_id: Optional[int] = None
    plugin_intent_threshold: float = 0.5
    plugins: List[PluginConfig] = field(default_factory=list)
    porcupine: PorcupineConfig = None

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
