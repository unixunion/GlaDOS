# GlaDOS — Voice-First Home Assistant

A voice-first home assistant with a pluggable architecture, LLM tool calling, knowledge retrieval, and a GLaDOS personality. Supports local LLMs (LM Studio, Ollama), Anthropic Claude, and any OpenAI-compatible API.

> WARNING! GLaDOS is maniacal and ultimately evil. Be careful connecting her to real-world systems. You have been warned — although the Three Laws of Robotics plugin should keep you safe. Probably.

## Features

- **Voice interface** — Whisper STT, switchable TTS (Piper/ONNX GlaDOS voice or Kokoro multi-voice), OpenWakeWord wake word detection
- **Plugin system** — 25+ tools auto-discovered from `plugins/`, registered via `@mcp_tool` decorator or `RunnableMCPPlugin` classes
- **Hybrid NLP+LLM** — high-confidence commands execute instantly via NLP (~5ms), ambiguous requests fall through to the LLM
- **Per-plugin NLP thresholds** — individual tools can set their own confidence threshold for fast-path routing
- **Multiple LLM backends** — OpenAI-compatible (LM Studio, Ollama), Anthropic Claude, LangChain
- **Knowledge base (RAG)** — Qdrant vector store with Wikipedia/ZIM ingestion, configurable query modes (raw, context-augmented, LLM rewrite)
- **Persistent memory** — ChromaDB-backed fact storage and conversation recall across sessions
- **Conversation RAG** — Qdrant-backed semantic retrieval of prior exchanges
- **Activity contexts** — separate message histories per activity (Cooking, Utilities, System, etc.) with per-activity tool filtering
- **Shopping list & pantry** — voice-managed shopping list, pantry inventory with expiry tracking, recipe integration
- **Recipe system** — 13.5K recipe dataset with images, fuzzy search, ingredient matching, positional selection ("the first one")
- **Timers & alarms** — durable (survive restarts), unified ringing with display overlay and dismiss buttons
- **Display UI** — responsive web dashboard (iPad/browser) with cards, full-screen views, chat drawer with pin, recipe images
- **Log analyzer** — ring buffer captures all logs, ask GlaDOS to analyze errors and save structured reports
- **GLaDOS personality** — SarcasmCore system prompt + PersonalityCore contextual quips
- **Vision** (POC) — camera feed to vision model with automatic tool triggering

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with voice (full mode)
python main.py

# Run in text mode (no microphone/speaker needed, fast iteration)
python main.py --no-speech

# Open the display UI
open http://localhost:5001
```

Requires a running LLM server. See [LLM Backends](#llm-backends) below.

## LLM Backends

GlaDOS works with any OpenAI-compatible API, Anthropic Claude, or LangChain:

```yaml
# Local model via LM Studio / Ollama
client_type: OPENAI
completion_url: "http://localhost:1234/v1"
model: "qwen/qwen3-30b-a3b-2507"
api_key: "lm-studio"

# Anthropic Claude (API key via env var ANTHROPIC_API_KEY)
client_type: ANTHROPIC
model: "claude-sonnet-4-20250514"
```

### Recommended Models

| Memory | Model | Notes |
|--------|-------|-------|
| **16GB** | Qwen 2.5 7B, Qwen 3 8B | Workable with <15 tools |
| **32GB** | Qwen 2.5 32B (Q4), Qwen 3 30B-A3B (MoE) | Good tool calling, 20+ tools |
| **64GB+** | Qwen 2.5 72B (Q4) | Excellent tool calling |

Run `python tests/benchmark_models.py --all` to benchmark models on your hardware.

## Architecture

```
Voice Input → Whisper STT → Wake Word Gate
    ↓
NLP Intent Classifier (fast-path, ~5ms)
    ↓ (high confidence)          ↓ (low confidence)
Direct tool execution           LLM with tool schemas
    ↓                               ↓
TTS Queue → Piper/Kokoro → Speaker
    ↓
Display UI (SocketIO) → iPad/Browser
```

Key components:
- **Chat pipeline hooks** — plugins register PRE_LLM and POST_RESPONSE hooks (memory, knowledge RAG, personality)
- **Event system** — pub/sub for inter-component communication (`system.tick`, `status.*`, `display.*`, etc.)
- **Activity system** — separate message contexts per activity with tool filtering
- **Streaming** — LLM responses streamed chunk-by-chunk, sentences sent to TTS as they complete

## Plugins

Plugins are auto-discovered from `plugins/` on startup. Two patterns:

```python
# Simple function plugin
@mcp_tool(
    description="Get current weather",
    parameters={"location": {"type": "string", "description": "City"}},
    intents=["what is the weather", "is it cold today"],
    process_output=True,
    nlp_threshold=0.6,  # per-tool NLP confidence override
)
def get_weather(location: str) -> str:
    return "Sunny, 25C"

# Stateful plugin with background processes
class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        self.register_tool(handler=self.do_thing, ...)
    def start(self):
        self.event_system.subscribe("system.tick", ...)
```

### Included Plugins

| Plugin | Description |
|--------|-------------|
| **CountdownTimer** | Durable timers with display overlay, unified ringing |
| **AlarmClock** | Fixed-time alarms with natural language parsing |
| **RecipeAPI** | 13.5K recipes, fuzzy search, images, positional selection |
| **PantryPlugin** | Shopping list, pantry inventory, expiry tracking, 12 tools |
| **MusicPlayer** | Spotify playback with fuzzy matching |
| **KnowledgeRAG** | Qdrant-backed Wikipedia/reference retrieval |
| **LogAnalyzer** | Ring buffer log capture, error analysis, saved reports |
| **PersonalityCore** | Contextual GLaDOS quips after responses |
| **SarcasmCore** | GLaDOS personality via system prompt |
| **LoopGuard** | Detects TTS repetition loops |

## Knowledge Base (RAG)

Retrieve factual knowledge from Qdrant (Wikipedia, manuals, any text corpus):

```bash
# Start Qdrant
docker run -d -p 6333:6333 -v $(pwd)/data/qdrant_storage:/qdrant/storage qdrant/qdrant

# Ingest Wikipedia
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia
```

Three query modes for optimal retrieval:

| Mode | How | Latency | Best for |
|------|-----|---------|----------|
| `raw` | Direct embedding | 0ms | Simple questions |
| `context` (default) | Prepend conversation history | ~0ms | Follow-up questions |
| `rewrite` | LLM reformulates query | 0.5-3s | Complex/ambiguous questions |

Benchmark: `python tests/benchmark_knowledge.py`

## Display UI

Web dashboard at `http://localhost:5001` designed for iPad:

- **Dashboard** with cards: Shopping List, Pantry, Recipes ("What can I make?"), Timers
- **Full-screen views** for each section with back navigation
- **Chat drawer** with pin to keep it open, collapsible knowledge/tool bubbles
- **Timer overlay** — floating countdown with dismiss buttons
- **Mute buttons** — silence TTS and/or microphone from the header
- **Mobile PWA** — installable shopping list at `/shopping`

## Testing

```bash
make test                     # Fast unit tests
make test-all                 # Unit + browser tests
make test-knowledge           # Knowledge RAG benchmark (needs Qdrant)
make test-knowledge-rewrite   # + LLM rewrite mode (needs LM Studio)
make test-models              # LLM tool-calling benchmark
```

## Configuration

All settings in `glados_config.yml`. Key sections:

| Section | Settings |
|---------|----------|
| **LLM** | `completion_url`, `model`, `client_type`, `api_key` |
| **Voice** | `voice_core`, `voice_model`, `speaker_id`, `speech_buffer_ms` |
| **NLP** | `hybrid_nlp_threshold`, `plugin_intent_threshold`, `nlp_mode` |
| **Knowledge** | `knowledge_enabled`, `knowledge_query_mode`, `knowledge_threshold` |
| **Memory** | `memory_enabled`, `memory_auto_store`, `memory_top_k` |
| **Plugins** | Per-plugin config blocks under `plugins:` |

See the [wiki](http://localhost:5001/wiki/) for full documentation.

## Installation

### Requirements

- Python 3.11+
- PortAudio (`brew install portaudio` / `apt install libportaudio2`)
- An LLM server (LM Studio recommended for local, or Anthropic API key)

### Setup

```bash
git clone https://github.com/unixunion/glados.git
cd glados
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: CUDA PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Optional: Qdrant for knowledge base
docker run -d -p 6333:6333 qdrant/qdrant

# Optional: Playwright for UI tests
pip install playwright pytest-playwright && python -m playwright install chromium
```

### Platform Notes

- **macOS** — works out of the box with Homebrew PortAudio
- **Linux** — `sudo apt install libportaudio2` for audio support
- **Windows** — run `install_windows.bat` for automated setup
