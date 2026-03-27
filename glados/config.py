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
    voice_core: str = "glados"  # "glados" (Piper) or "kokoro"
    speaker_id: Optional[str | int] = None
    display_port: int = 5001
    thinking_enabled: bool = False  # Allow models to use [THINK] reasoning tags; disable for faster responses
    max_context_messages: int = 20  # Max messages per activity context; older messages are trimmed to keep context small and fast
    music_dir: Optional[str] = None  # Path to music directory for the music player plugin
    speech_buffer_ms: int = BUFFER_SIZE  # Milliseconds of silence before finalizing speech; also sets the pre-wake audio buffer size
    plugin_intent_threshold: float = 0.5
    plugins: List[PluginConfig] = field(default_factory=list)
    porcupine: PorcupineConfig = None
    openwakeword: Optional[dict] = None
    memory_enabled: bool = False
    memory_auto_store: bool = False  # Auto-store every exchange. False = only explicit "remember that..." facts.
    memory_db_path: str = "data/memory_db"
    memory_top_k: int = 5
    # Knowledge base (Qdrant RAG)
    knowledge_enabled: bool = False
    qdrant_url: str = "http://localhost:6333"
    knowledge_collections: list = None  # Qdrant collections to search, e.g. ["wikipedia"]
    knowledge_top_k: int = 3
    knowledge_threshold: float = 0.5
    knowledge_embed_model: str = "all-MiniLM-L6-v2"
    # Conversation RAG (Qdrant-backed conversation retrieval)
    conversation_rag_enabled: bool = False
    conversation_rag_top_k: int = 5
    conversation_rag_threshold: float = 0.4
    nlp_mode: bool = False
    nlp_confidence_threshold: float = 0.4
    hybrid_nlp_threshold: float = 0.8  # NLP fast-path threshold; if intent confidence >= this, bypass LLM. Set to 1.0 to disable.
    max_response_tokens: int = 500  # Max tokens per LLM text response (not tool calls). Prevents runaway generation.
    max_response_time: int = 15  # Max seconds for LLM response streaming (wall-clock abort)
    max_tool_depth: int = 2  # Max recursive tool call depth (0=user query, 1=first tool result, 2=retry)
    tts_buffer_mode: str = "clause"  # "sentence" (wait for .!?), "clause" (split on ,;:— too), "word" (every N words)
    tts_word_buffer: int = 5  # words per flush in "word" mode
    power_on_prompt: Optional[str] = "You have just been powered on. Greet the user in one sentence."  # Sent to LLM on startup. Set to null to disable.
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
