from dataclasses import dataclass
from typing import Optional, List, Sequence

import yaml

VAD_MODEL = "silero_vad.onnx"
VOICE_MODEL = "glados.onnx"
PAUSE_TIME = 0.05  # Time to wait between processing loops
SAMPLE_RATE = 16000  # Sample rate for input stream
VAD_SIZE = 50        # Milliseconds of sample for Voice Activity Detection (VAD)
VAD_THRESHOLD = 0.9  # Threshold for VAD detection
BUFFER_SIZE = 700  # Milliseconds of buffer before VAD detection
PAUSE_LIMIT = 500  # Milliseconds of pause allowed before processing
SIMILARITY_THRESHOLD = 2  # Threshold for wake word similarity

NEUROTOXIN_RELEASE_ALLOWED = False  # preparation for function calling, see issue #13
DEFAULT_PERSONALITY_PREPROMPT = (
    {
        "role": "system",
        "content": "You are a helpful AI assistant. You are here to assist the user in their tasks.",
    },
)


@dataclass
class GladosConfig:
    completion_url: str
    model: str
    api_key: Optional[str]
    wake_word: Optional[str]
    announcement: Optional[str]
    personality_preprompt: List[dict[str, str]]
    interruptible: bool
    intents: List[dict]
    intent_confidence_threshold: int
    voice_model: str = VOICE_MODEL
    speaker_id: Optional[int] = None

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