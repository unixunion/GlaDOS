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
  basic/                   # clock, timers, alarms, memory_tools (system prompt only)
  recipes/                 # Recipe search and selection
  music/                   # Spotify playback control
  display/                 # Flask+SocketIO web display for iPad/browser
  chores/                  # Vacuum control
  vision/                  # Camera/vision model integration (POC)
  system/                  # list_plugins, get_logs

models/                    # TTS model files (glados.onnx, kokoro-82m-onnx/)
data/memory_db/            # ChromaDB persistent storage (auto-created)
glados_config.yml          # All runtime configuration
main.py                    # Entry point — wires everything together
```

## Architecture Patterns

- **Singletons**: `PluginSystem`, `EventSystem`, `IntentClassifier`, `VectorMemoryStore` are all singletons
- **Event-driven**: components communicate via `EventSystem` pub/sub (topic-based with fnmatch patterns)
- **Activity contexts**: `MessageManager` keeps separate message histories per activity (GENERAL, COOKING, etc.). The intent classifier routes user input to the right context, and tools are filtered per activity.
- **Plugin patterns**: Two types — `@mcp_tool` decorated functions (simple) and `RunnableMCPPlugin` subclasses (stateful, with start/stop lifecycle). Both auto-register with the MCP server.
- **Pre-LLM interception**: Memory "remember"/"recall" intents are detected by the `IntentClassifier` (same Naive Bayes classifier used for all tool routing) before the LLM runs. The `MemoryTools` plugin registers `_memory_remember` and `_memory_recall` intents with training examples at startup. `ChatClient._detect_memory_intent()` checks the classifier, then stores/retrieves directly and injects results as system messages. This avoids depending on the LLM to call tools.
- **Streaming**: LLM responses are streamed chunk-by-chunk. `ResponseProcessor` accumulates text and sends complete sentences to the TTS queue for low-latency voice output.

## Memory System

The memory system in `glados/llm/memory/store.py` uses ChromaDB PersistentClient. Key design decisions:

- **Two document types**: `exchange` (automatic user+assistant pairs) and `fact` (explicit "remember that...")
- **Dual retrieval**: `retrieve()` runs two queries — activity-filtered exchanges + explicit facts — merged and deduplicated. This ensures facts like "I prefer celsius" surface in any activity context.
- **Intent detection**: the `MemoryTools` plugin (`plugins/basic/memory_tools.py`) registers `_memory_remember` and `_memory_recall` intents with the shared `IntentClassifier` at startup. `ChatClient._detect_memory_intent()` checks the classifier prediction — if "remember" intent, regex extracts the fact content and `store_fact()` persists it; if "recall" intent, `search()` does a broad unfiltered query. Results are injected as system messages so the LLM just confirms/answers naturally.
- **Regex role**: only used for fact *extraction* (pulling "I prefer celsius" from "remember that I prefer celsius"), not for intent detection. Falls back to the full utterance if no pattern matches.
- **Plugin** (`plugins/basic/memory_tools.py`): registers intents with the IntentClassifier and provides a system prompt. No LLM tools registered — memory ops are entirely pre-LLM.

## Standards

- Use `loguru` for all logging (not stdlib `logging`)
- Plugin tools use the `@mcp_tool` decorator or `RunnableMCPPlugin.register_tool()`
- Config is a flat `@dataclass` loaded from YAML — add new fields with defaults to `GladosConfig`
- Prefix memory log lines with `[Memory]` (chat_client) or `[MemoryStore]` (store.py) for easy filtering
- All TTS output must be natural spoken language — no markdown, no special characters

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
