# Architecture

A modular re-write of the upstream project with plugin support, function calling, and event-driven communication.

## Features

- Pre-prompted with the 3 laws of robotics
- Plugin support with long-running processes via `RunnableMCPPlugin`
- Function/tool calling — GlaDOS can interact with external systems via LLM tool calls
- Intent classification — plugins define intent strings to help route user requests to the right tool
- Event system — plugins, functions, and architecture components communicate via pub/sub events
- Vision support (POC) — images can be base64-encoded and sent to a vision model
- Whisper for speech-to-text
- MCP (Model Context Protocol) for standardized tool registration

## Activity System

The activity system provides separate message contexts per activity with tool filtering so the LLM only sees relevant tools for the current context.

**Flow:** User Input → IntentClassifier predicts tool → tool's activity → switch_context → filter tools → LLM call

| Activity | Tools |
|----------|-------|
| GENERAL | weather, time, recipes, display, alarms, list_plugins |
| COOKING | recipes, timers, alarms, display, time |
| UTILITIES | weather, timers, alarms, display, time |
| CHORES | vacuum, display |
| SYSTEM | time, logs, list_plugins |
| ENTERTAINMENT | music player |

The system prompt is shared across all activity contexts. The display UI shows the current activity as a pill icon in the top-left corner.

## Event System

The EventSystem is a singleton pub-sub system using topic-based subscriptions with `fnmatch` pattern matching.

| Topic | Description |
|-------|-------------|
| `system.tick` | 1Hz heartbeat |
| `system.wake_word_detected` | Wake word fired |
| `system.listen_for_response` | TTS finished, auto-listen enabled |
| `system.interrupt_tts` | Wake word during TTS, interrupt speech |
| `system.music_pause` / `system.music_resume` | Alarm pauses/resumes music |
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
    → LLM called with filtered tools → Tool call or text response
    → Tool executed → Result to LLM (if process_output) or TTS (if not)
    → TTS generates speech → Audio plays → Listen for follow-up
```
