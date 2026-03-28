# Project Context

When working with this codebase, prioritize readability over cleverness. Ask clarifying questions before making architectural changes.

## About This Project

GlaDOS is a voice-first home assistant with a pluggable architecture. It connects to a local LLM (via OpenAI-compatible API), uses Whisper for speech-to-text, Piper/Kokoro for TTS, and has a plugin system for tools (timers, alarms, recipes, music, display, memory, vision).

## Key Directories

```
glados/                    # Core application code
  llm/
    cores/chat_client.py   # Main LLM orchestration — chat loop, memory interception, tool execution
    stream_handler.py      # Builds and sends requests to the LLM, injects memory context
    message_manager.py     # Per-activity message contexts with rolling window
    response_processor.py  # Streams LLM output to TTS sentence-by-sentence
    memory/store.py        # ChromaDB-backed persistent vector memory (VectorMemoryStore singleton)
    voice_cores/           # TTS backends (Piper ONNX, Kokoro ONNX)
    speech_detection_cores/ # Wake word detection, Whisper STT
  context/activity.py      # Activity enum (GENERAL, COOKING, UTILITIES, CHORES, SYSTEM, ENTERTAINMENT)
  mcp/                     # MCP tool registration layer (decorators, server, metadata registry)
  system/                  # Plugin system, event system, intent classifier
  config.py                # GladosConfig dataclass, loaded from glados_config.yml

plugins/                   # Auto-discovered plugins (walked recursively on startup)
  basic/                   # clock, timers, alarms, unit converter
  recipes/                 # Recipe search and selection (13.5K recipes with images, fuzzy ingredient matching)
  pantry/                  # Shopping list + pantry inventory, expiry tracking, recipe integration
  music/                   # Spotify playback control
  display/                 # Flask+SocketIO web display for iPad/browser
  chores/                  # Vacuum control
  vision/                  # Camera/vision model integration (POC)
  system/                  # list_plugins, get_logs, loop_guard
  cores/                   # personality cores (sarcasm, memory, three laws, neurotoxin, personality quips)
  knowledge/               # RAG retrieval plugin (Qdrant)

models/                    # TTS model files (glados.onnx, kokoro-82m-onnx/)
data/recipes/              # Recipe dataset CSV + images (data/recipes/img/Food Images/)
data/memory_db/            # ChromaDB persistent storage (auto-created)
data/glados_quotes/        # Themed GLaDOS personality quotes for PersonalityCore
plugin_data/pantry/        # Shopping list + pantry JSON persistence
tools/                     # Offline utilities (ingest_zim.py for Qdrant ingestion)
tests/                     # NLP test suite, model benchmarks
glados_config.yml          # All runtime configuration
main.py                    # Entry point — wires everything together
```

## Architecture Patterns

- **Singletons**: `PluginSystem`, `EventSystem`, `IntentClassifier`, `VectorMemoryStore` are all singletons
- **Event-driven**: components communicate via `EventSystem` pub/sub (topic-based with fnmatch patterns)
- **Activity contexts**: `MessageManager` keeps separate message histories per activity (GENERAL, COOKING, etc.). The intent classifier routes user input to the right context, and tools are filtered per activity.
- **Plugin patterns**: Two types — `@mcp_tool` decorated functions (simple) and `RunnableMCPPlugin` subclasses (stateful, with start/stop lifecycle). Both auto-register with the MCP server.
- **Pre-LLM interception**: Memory operations are handled by `MemoryCore` (`plugins/cores/memory_core.py`) via a PRE_LLM chat pipeline hook. The plugin registers `_memory_remember`, `_memory_recall`, `_memory_forget_all`, and `_memory_debug` intents with the IntentClassifier. When detected, memory operations execute directly — no LLM involvement. Results are injected as system messages or spoken via TTS.
- **Hybrid NLP+LLM mode**: When `hybrid_nlp_threshold < 1.0` (default 0.8), the IntentClassifier runs before the LLM. High-confidence matches execute the tool instantly via NLP (~5ms); tools with `process_output=True` still use the LLM for natural summarization. Low-confidence inputs fall through to the normal LLM path. This gives sub-100ms tool execution for clear commands.
- **Streaming**: LLM responses are streamed chunk-by-chunk. `ResponseProcessor` accumulates text and sends complete sentences to the TTS queue for low-latency voice output.

## Memory System

The memory system in `glados/llm/memory/store.py` uses ChromaDB PersistentClient. Key design decisions:

- **Two document types**: `exchange` (automatic user+assistant pairs) and `fact` (explicit "remember that...")
- **Dual retrieval**: `retrieve()` runs two queries — activity-filtered exchanges + explicit facts — merged and deduplicated. This ensures facts like "I prefer celsius" surface in any activity context.
- **Intent detection**: `MemoryCore` (`plugins/cores/memory_core.py`) registers intents and handles all memory logic via a PRE_LLM chat pipeline hook. Remember/recall/forget/debug intents are detected by the IntentClassifier, then executed directly (store fact, search, clear, or dump to log).
- **Regex role**: only used for fact *extraction* (pulling "I prefer celsius" from "remember that I prefer celsius"), not for intent detection. Falls back to the full utterance if no pattern matches.
- **Plugin** (`plugins/cores/memory_core.py`): registers intents, provides a system prompt explaining memory to the LLM, and handles all memory operations via chat hook. No LLM tools registered — memory ops are entirely pre-LLM.

## Standards

- Use `loguru` for all logging (not stdlib `logging`)
- Plugin tools use the `@mcp_tool` decorator or `RunnableMCPPlugin.register_tool()`
- Config is a flat `@dataclass` loaded from YAML — add new fields with defaults to `GladosConfig`
- Prefix memory log lines with `[Memory]` (chat_client) or `[MemoryStore]` (store.py) for easy filtering
- All TTS output must be natural spoken language — no markdown, no special characters

## Plugin Containment Rules

**ALL plugin logic MUST be self-contained within the plugin.** Never add plugin-specific code to core files (`display_server.py`, `chat_client.py`, `main.py`). Use the registration APIs instead:

| Need | Use | NOT |
|------|-----|-----|
| LLM tool | `self.register_tool()` | Editing `chat_client.py` |
| System prompt | `self.register_system_prompt()` | Editing system prompts elsewhere |
| Chat pipeline hook | `self.register_chat_hook()` | Modifying `chat()` directly |
| Display UI action (button, form) | `self.register_ui_action()` | Adding SocketIO handlers to `display_server.py` |
| NLP intents | `intents=` param on `register_tool()` | Editing `intent_classifier.py` |
| Event handling | `self.event_system.subscribe()` | Modifying event publishers |

### UI Action Pattern

For UI-driven actions (clicks, forms, widgets), use `register_ui_action()`:

```python
# In plugin __init__:
self.register_ui_action("my_action", self._on_my_action)

# In plugin start():
self.event_system.subscribe("ui.my_action", EventHook("handler", callback=self._on_my_action))
```

Frontend emits directly to the plugin's event — no `user_message` → NLP → LLM round-trip:
```javascript
socket.emit('my_action', { action: 'do_thing', param: 'value' });
```

### When to use LLM vs. direct action

- **UI button/form** → `register_ui_action()` (direct, instant)
- **Clear voice command** → NLP fast-path via `register_tool()` with intents
- **Ambiguous voice input** → LLM tool via `register_tool()` with `process_output=True`
- **Data injection (not spoken)** → publish event with `process_output=False`, inject as system context
- **Never** return large data (ingredients, directions) in tool results — the LLM will read it aloud. Put it in context tags instead (`<active_recipe>`, `<knowledge>`).

## Common Commands

```bash
# Run with voice (full mode)
python main.py

# Run in text mode (no microphone/speaker needed, fast iteration)
python main.py --no-speech

# Run tests
pytest tests/
```

## Notes

- The LLM server is external (LM Studio, Ollama, or any OpenAI-compatible API). GlaDOS does not run the model itself.
- Small local models (<7B) often fail to call tools reliably — that's why the memory system uses pre-LLM interception instead of tool calls.
- The `_recursive` flag in `chat()` prevents memory storage/retrieval on tool-result processing loops.
- ChromaDB's `all-MiniLM-L6-v2` embedding model (~90MB) downloads automatically on first use.
- The display wiki at `plugins/display/wiki/` is served by the display plugin's Flask server.
