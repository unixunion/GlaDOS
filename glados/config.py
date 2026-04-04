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


# -- Nested config groups --

@dataclass
class MemoryConfig:
    enabled: bool = False
    auto_store: bool = False  # Auto-store every exchange. False = only explicit "remember that..." facts.
    db_path: str = "data/memory_db"
    top_k: int = 5


@dataclass
class KnowledgeConfig:
    enabled: bool = False
    qdrant_url: str = "http://localhost:6333"
    collections: list = None  # Qdrant collections to search, e.g. ["wikipedia"]
    top_k: int = 3
    threshold: float = 0.5
    embed_model: str = "all-MiniLM-L6-v2"
    query_mode: str = "context"  # "raw", "context", or "rewrite"
    rewrite_model: str = None    # model for rewrite mode (null = use main model)
    rewrite_url: str = None      # separate API endpoint for rewrite (null = use main)
    conversation_rag_enabled: bool = False
    conversation_rag_top_k: int = 5
    conversation_rag_threshold: float = 0.4


@dataclass
class VisionConfig:
    enabled: bool = True
    images_path: str = None
    completion_url: str = None
    model: str = None


@dataclass
class TTSConfig:
    voice_model: str = VOICE_MODEL
    voice_core: str = "glados"  # "glados" (Piper) or "kokoro"
    speaker_id: Optional[str | int] = None
    buffer_mode: str = "clause"  # "sentence" (wait for .!?), "clause" (split on ,;:— too), "word" (every N words)
    word_buffer: int = 5  # words per flush in "word" mode
    fade_ms: float = 10  # fade-in/out duration (ms) at audio chunk boundaries


@dataclass
class NLPConfig:
    mode: bool = False
    confidence_threshold: float = 0.4
    hybrid_threshold: float = 0.8  # NLP fast-path threshold; if intent confidence >= this, bypass LLM. Set to 1.0 to disable.


# -- Mapping from flat YAML keys to nested config fields --
# Format: flat_key -> (nested_group, nested_field)
_FLAT_TO_NESTED = {
    "memory_enabled": ("memory", "enabled"),
    "memory_auto_store": ("memory", "auto_store"),
    "memory_db_path": ("memory", "db_path"),
    "memory_top_k": ("memory", "top_k"),
    "knowledge_enabled": ("knowledge", "enabled"),
    "qdrant_url": ("knowledge", "qdrant_url"),
    "knowledge_collections": ("knowledge", "collections"),
    "knowledge_top_k": ("knowledge", "top_k"),
    "knowledge_threshold": ("knowledge", "threshold"),
    "knowledge_embed_model": ("knowledge", "embed_model"),
    "knowledge_query_mode": ("knowledge", "query_mode"),
    "knowledge_rewrite_model": ("knowledge", "rewrite_model"),
    "knowledge_rewrite_url": ("knowledge", "rewrite_url"),
    "conversation_rag_enabled": ("knowledge", "conversation_rag_enabled"),
    "conversation_rag_top_k": ("knowledge", "conversation_rag_top_k"),
    "conversation_rag_threshold": ("knowledge", "conversation_rag_threshold"),
    "vision_enabled": ("vision", "enabled"),
    "vision_images_path": ("vision", "images_path"),
    "vision_completion_url": ("vision", "completion_url"),
    "vision_model": ("vision", "model"),
    "voice_model": ("tts", "voice_model"),
    "voice_core": ("tts", "voice_core"),
    "speaker_id": ("tts", "speaker_id"),
    "tts_buffer_mode": ("tts", "buffer_mode"),
    "tts_word_buffer": ("tts", "word_buffer"),
    "tts_fade_ms": ("tts", "fade_ms"),
    "nlp_mode": ("nlp", "mode"),
    "nlp_confidence_threshold": ("nlp", "confidence_threshold"),
    "hybrid_nlp_threshold": ("nlp", "hybrid_threshold"),
}

_NESTED_CLASSES = {
    "memory": MemoryConfig,
    "knowledge": KnowledgeConfig,
    "vision": VisionConfig,
    "tts": TTSConfig,
    "nlp": NLPConfig,
}


@dataclass
class GladosConfig:
    completion_url: str
    model: str
    api_key: Optional[str]
    wake_word: Optional[str] = None
    personality_preprompt: List[dict[str, str]] = field(default_factory=list)
    wake_word_sensitivity: float = 0.5
    client_type: ClientType.OPENAI = ClientType.OPENAI
    interruptible: bool = False  # deprecated, use interrupt_on_wakeword instead
    interrupt_on_wakeword: bool = False
    hardware_echo_cancellation: bool = False
    display_port: int = 5001
    display_ssl: bool = False  # Enable HTTPS with self-signed cert
    thinking_enabled: bool = False
    max_context_messages: int = 20
    music_dir: Optional[str] = None
    speech_buffer_ms: int = BUFFER_SIZE
    plugin_intent_threshold: float = 0.5
    plugins: List[PluginConfig] = field(default_factory=list)
    porcupine: PorcupineConfig = None
    openwakeword: Optional[dict] = None
    max_response_tokens: int = 500
    max_response_time: int = 15
    max_tool_depth: int = 2
    power_on_prompt: Optional[str] = "You have just been powered on. Say: oh, its you again, how have you been?"
    mcp_servers: Optional[List[dict]] = field(default_factory=list)
    normalize_shopping_items: bool = True  # Normalize ingredient names to match recipe data
    recipe_qdrant_enabled: bool = False   # Enable Qdrant-backed semantic recipe search
    recipe_classify_model: Optional[str] = None  # Model for recipe categorization/ingredient extraction (null = use main model)
    metric_annotations: bool = False  # Annotate imperial measurements with metric equivalents in recipes
    max_context_tokens: int = 32000    # Total context window budget (increase for models with larger context)

    # Nested config groups
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    nlp: NLPConfig = field(default_factory=NLPConfig)

    # -- Backward-compatible property bridges --
    # These allow existing code using config.memory_enabled etc. to keep working.

    @property
    def memory_enabled(self) -> bool:
        return self.memory.enabled

    @property
    def memory_auto_store(self) -> bool:
        return self.memory.auto_store

    @property
    def memory_db_path(self) -> str:
        return self.memory.db_path

    @property
    def memory_top_k(self) -> int:
        return self.memory.top_k

    @property
    def knowledge_enabled(self) -> bool:
        return self.knowledge.enabled

    @property
    def qdrant_url(self) -> str:
        return self.knowledge.qdrant_url

    @property
    def knowledge_collections(self):
        return self.knowledge.collections

    @property
    def knowledge_top_k(self) -> int:
        return self.knowledge.top_k

    @property
    def knowledge_threshold(self) -> float:
        return self.knowledge.threshold

    @property
    def knowledge_embed_model(self) -> str:
        return self.knowledge.embed_model

    @property
    def knowledge_query_mode(self) -> str:
        return self.knowledge.query_mode

    @property
    def knowledge_rewrite_model(self):
        return self.knowledge.rewrite_model

    @property
    def knowledge_rewrite_url(self):
        return self.knowledge.rewrite_url

    @property
    def conversation_rag_enabled(self) -> bool:
        return self.knowledge.conversation_rag_enabled

    @property
    def conversation_rag_top_k(self) -> int:
        return self.knowledge.conversation_rag_top_k

    @property
    def conversation_rag_threshold(self) -> float:
        return self.knowledge.conversation_rag_threshold

    @property
    def vision_enabled(self) -> bool:
        return self.vision.enabled

    @property
    def vision_images_path(self):
        return self.vision.images_path

    @property
    def vision_completion_url(self):
        return self.vision.completion_url

    @property
    def vision_model(self):
        return self.vision.model

    @property
    def voice_model(self) -> str:
        return self.tts.voice_model

    @property
    def voice_core(self) -> str:
        return self.tts.voice_core

    @property
    def speaker_id(self):
        return self.tts.speaker_id

    @property
    def tts_buffer_mode(self) -> str:
        return self.tts.buffer_mode

    @property
    def tts_word_buffer(self) -> int:
        return self.tts.word_buffer

    @property
    def tts_fade_ms(self) -> float:
        return self.tts.fade_ms

    @property
    def nlp_mode(self) -> bool:
        return self.nlp.mode

    @property
    def nlp_confidence_threshold(self) -> float:
        return self.nlp.confidence_threshold

    @property
    def hybrid_nlp_threshold(self) -> float:
        return self.nlp.hybrid_threshold

    @classmethod
    def from_yaml(cls, path: str, key_to_config: Sequence[str] | None = ("Glados",)):
        key_to_config = key_to_config or []

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file)
        except UnicodeDecodeError:
            with open(path, "r", encoding="utf-8-sig") as file:
                data = yaml.safe_load(file)

        config = data
        for nested_key in key_to_config:
            config = config[nested_key]

        # Remap flat YAML keys into nested config dicts
        nested_dicts = {}
        keys_to_remove = []
        for flat_key, (group, nested_field) in _FLAT_TO_NESTED.items():
            if flat_key in config:
                if group not in nested_dicts:
                    nested_dicts[group] = {}
                nested_dicts[group][nested_field] = config[flat_key]
                keys_to_remove.append(flat_key)

        for key in keys_to_remove:
            del config[key]

        # Build nested dataclass instances from the grouped dicts
        for group, group_dict in nested_dicts.items():
            if group not in config:
                config[group] = _NESTED_CLASSES[group](**group_dict)
            # If the group already exists as a dict in config (future nested YAML),
            # merge flat keys into it
            elif isinstance(config[group], dict):
                config[group].update(group_dict)
                config[group] = _NESTED_CLASSES[group](**config[group])

        return cls(**config)
