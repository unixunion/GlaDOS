# Architecture

A modular re-write of the upstream project with plugin support, function calling, and event-driven communication.

## Features

- Pre-prompted with the 3 laws of robotics
- Plugin support with long-running processes via `RunnableMCPPlugin`
- Function/tool calling — GlaDOS can interact with external systems via LLM tool calls
- Intent classification — plugins define intent strings to help route user requests to the right tool
- Hybrid NLP+LLM mode — high-confidence commands execute instantly via NLP, ambiguous requests fall through to the LLM
- Chat pipeline hooks — plugins can register PRE_LLM and POST_RESPONSE hooks to intercept and modify the chat flow
- Event system — plugins, functions, and architecture components communicate via pub/sub events
- Vision support (POC) — images can be base64-encoded and sent to a vision model
- Whisper for speech-to-text
- Switchable TTS voice cores (Piper/ONNX or Kokoro ONNX) via config, with cooking abbreviation expansion (Tbsp → tablespoon, etc.)
- MCP (Model Context Protocol) for standardized tool registration
- Persistent vector memory via ChromaDB — explicit fact storage and optional auto-exchange storage
- Knowledge base RAG via Qdrant — ingest ZIM files (Wikipedia, StackOverflow) for factual question answering
- Conversation RAG via Qdrant — stores exchanges and retrieves relevant prior conversations for context
- Response safeguards — max token limit, wall-clock timeout, LoopGuard repetition detection
- GLaDOS personality system — SarcasmCore (LLM prompt) + PersonalityCore (contextual quip injection)

## Activity System

The activity system provides separate message contexts per activity with tool filtering so the LLM only sees relevant tools for the current context.

**Flow:** User Input → IntentClassifier predicts tool → tool's activity → switch_context → filter tools → LLM call

| Activity | Tools |
|----------|-------|
| GENERAL | weather, time, recipes, display, alarms, list_plugins, memory tools |
| COOKING | recipes, timers, alarms, display, time |
| UTILITIES | weather, timers, alarms, display, time |
| CHORES | vacuum, display |
| SYSTEM | time, logs, list_plugins, memory tools |
| ENTERTAINMENT | music player |

The system prompt is shared across all activity contexts. The display UI shows the current activity as a pill icon in the top-left corner.

## Event System

The EventSystem is a singleton pub-sub system using topic-based subscriptions with `fnmatch` pattern matching.

| Topic | Description |
|-------|-------------|
| `system.tick` | 1Hz heartbeat |
| `system.wake_word_detected` | Wake word fired |
| `system.listen_for_response` | TTS finished, auto-listen enabled |
| `system.interrupt_tts` | Interrupt TTS playback — fired by wake word detection or the display UI Stop button. Flushes TTS queue, aborts audio, publishes idle status. |
| `system.music_pause` / `system.music_resume` | Alarm pauses/resumes music |
| `tts.speak` | Send text directly to TTS without LLM processing. Handled by ChatClient, which puts the text on the tts_queue. Works in all modes (speech or text-only console drain). |
| `tool.*` | Tool execution results |
| `display.*` | Content updates to the display screen |
| `status.*` | UI status toasts (listening, thinking, speaking, tool_call, idle, activity, user_speech) |
| `vision.*` | Vision model requests and responses |
| `log.*` | Error and diagnostic logs |

### Subscribing

```python
event_system = EventSystem()
event_system.subscribe(
   "system.listen_for_response",
   EventHook(name="my_hook", callback=self.on_response, priority=1)
)
```

### Publishing

```python
event_system.publish(
   EventMessage(
       role="tool",
       name="hello_world",
       content={"message": "Hello from a plugin!"},
       process_output=True  # tells the LLM to parse this payload immediately
   )
)
```

## Data Flow

```
User speaks → Wake word detected → Whisper transcribes
    → IntentClassifier predicts tool → Activity inferred
    → MessageManager switches context → Tools filtered by activity
    → Vector memory retrieves relevant past exchanges → Injected as system message
    → LLM called with filtered tools + memory context → Tool call or text response
    → Tool executed → Result to LLM (if process_output) or TTS (if not)
    → Exchange stored in vector memory
    → TTS generates speech → Audio plays → Listen for follow-up
```

## Persistent Memory

GlaDOS uses ChromaDB (file-based PersistentClient) for persistent cross-session memory. All memory operations are handled **pre-LLM** by the application layer — no LLM tool calls required.

**How it works:**

1. **Automatic storage**: each user+assistant exchange is stored with metadata (activity, session_id, timestamp, memory_type)
2. **Automatic retrieval**: before each LLM call, the top-k semantically similar past exchanges are retrieved and injected as a system message. Uses dual-query (activity-filtered + explicit facts) to ensure stored preferences are always surfaced.
3. **Pre-LLM "remember"**: the `IntentClassifier` (same Naive Bayes used for tool routing) detects "remember" intent, then regex extracts the fact content. Stored directly via `store_fact()`. LLM receives a system message to confirm naturally.
4. **Pre-LLM "recall"**: the `IntentClassifier` detects "recall" intent, triggers a broad unfiltered `search()`, results injected as context for the LLM to formulate a response.

**Key details:**
- Intent detection: `MemoryTools` plugin registers `_memory_remember` and `_memory_recall` intents with the `IntentClassifier` at startup. `ChatClient._detect_memory_intent()` checks the classifier prediction and routes accordingly.
- Embedding: `all-MiniLM-L6-v2` via ChromaDB (cosine similarity, distance > 1.5 filtered out)
- Two document types: `exchange` (automatic) and `fact` (explicit "remember that...")
- Config: `memory_enabled`, `memory_db_path`, `memory_top_k` in `glados_config.yml`
- Data stored in `data/memory_db/`
- Plugin (`MemoryTools`) registers intents and provides system prompt — no LLM tools registered
