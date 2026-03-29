# Writing Plugins

Plugins can be simple decorated functions or stateful classes with background processes.

## Simple Function Plugin (`@mcp_tool`)

```python
from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool

@mcp_tool(
    description="Get current weather for a location.",
    parameters={"location": {"type": "string", "description": "City name"}},
    required=["location"],
    intents=["what is the weather", "is it cold today"],
    activity=[Activity.GENERAL, Activity.UTILITIES],
    process_output=True,
    system_prompt="When reporting weather, include temperature and conditions.",
)
def handle_weather(location: str) -> str:
    return f"Sunny, 25C in {location}"
```

## Stateful Plugin (`RunnableMCPPlugin`)

For plugins with background processes, event subscriptions, or lifecycle management:

```python
from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventHook, EventMessage

class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()

        # Config auto-loaded from glados_config.yml
        self.greeting = self.plugin_config.get("greeting", "hello")

        # Guide the LLM
        self.register_system_prompt("When greeting, always use the user's name.")

        self.register_tool(
            handler=self.hello_world,
            description="Greets someone by name",
            parameters={"name": {"type": "string", "description": "Name to greet"}},
            required=["name"],
            intents=["hello world", "greet someone"],
            process_output=True,
            activity=[Activity.GENERAL],
        )

    def start(self):
        self.event_system.subscribe(
            "system.tick",
            EventHook("my_tick", callback=self._on_tick, priority=1)
        )

    def stop(self):
        self.event_system.unsubscribe("system.tick", "my_tick")

    def hello_world(self, name: str):
        return {"status": "success", "content": f"{self.greeting} {name}"}

    def _on_tick(self, event: EventMessage):
        pass
```

## Plugin Configuration

Plugins load config from `glados_config.yml` under the `plugins` key. The `name` field is matched against the **class name** (case-insensitive, underscores ignored):

```yaml
plugins:
  - name: music_player       # matches class MusicPlayer
    config:
      default_volume: 50
  - name: sarcasm_core        # matches class SarcasmCore
    config:
      enabled: true
  - name: three_laws          # matches class ThreeLaws
    config:
      enabled: true
```

- **RunnableMCPPlugin**: auto-loaded into `self.plugin_config`
- **Function plugins**: `RunnableMCPPlugin.get_plugin_config("name")`

## System Prompt Additions

Plugins can append text to the LLM system prompt:

- `@mcp_tool(system_prompt="...")` — decorator param
- `self.register_system_prompt("...")` — method on RunnableMCPPlugin

These are injected as system messages into all activity contexts.

## Registration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `description` | str | What the tool does (shown to LLM) |
| `parameters` | dict | JSON Schema properties for arguments |
| `required` | list | Required parameter names |
| `intents` | list | Example phrases for IntentClassifier training |
| `activity` | list | Activity contexts where this tool is available |
| `process_output` | bool | True = LLM processes result, False = result goes direct to TTS |
| `system_prompt` | str | Text appended to LLM system prompt (decorator only) |
| `nlp_extractors` | dict | NLP mode: param name → regex patterns for extraction |
| `nlp_response` | callable | NLP mode: formats tool result as spoken text |
| `nlp_extract_fn` | callable | NLP mode: custom function to extract params from text |
| `nlp_threshold` | float | Per-tool NLP confidence threshold (overrides global `hybrid_nlp_threshold`) |

The `nlp_*` parameters enable [NLP mode](nlp-mode.md) support, allowing the tool to work without an LLM. See the [NLP Mode](nlp-mode.md) page for details.

## Adding NLP Support

NLP support lets your tool work in NLP mode (without an LLM). There are three approaches depending on complexity:

### Parameterless tools — just add `nlp_response`

```python
@mcp_tool(
    description="Get current time",
    intents=["what is the time", "what time is it"],
    nlp_response=lambda r: f"The time is {json.loads(r)['time']}.",
)
def get_current_time() -> str:
    return json.dumps({"time": datetime.now().strftime("%H:%M:%S")})
```

### Regex extraction — for simple parameter patterns

Use `nlp_extractors` with named groups matching your parameter names:

```python
@mcp_tool(
    description="Get weather for a location.",
    parameters={"location": {"type": "string", "description": "City name"}},
    required=["location"],
    intents=["what is the weather", "weather in london"],
    nlp_extractors={
        "location": [
            re.compile(r"\b(?:in|for|at)\s+(?P<location>.+?)$", re.IGNORECASE),
        ],
    },
    nlp_response=lambda r: f"The weather is {r}.",
)
def handle_weather(location: str) -> str:
    ...
```

### Custom extraction — for complex parsing

Use `nlp_extract_fn` when regex isn't enough:

```python
def _convert_extract(text: str) -> dict:
    m = re.search(r"(\d+)\s+(\w+)\s+(?:to|in)\s+(\w+)", text)
    if m:
        return {"value": float(m.group(1)), "from_unit": m.group(2), "to_unit": m.group(3)}
    return {}

@mcp_tool(
    description="Convert units",
    parameters={...},
    intents=["convert 100 fahrenheit to celsius", ...],
    nlp_extract_fn=_convert_extract,
    nlp_response=lambda r: r.get("message", "Done."),
)
def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    ...
```

### NLP-only handlers (no LLM tool)

For commands that don't call external tools (like cooking step navigation), register directly with the handler registry. These auto-register at import time during `load_plugins()`:

```python
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry
from glados.system.intent_classifier import IntentClassifier

def register_my_intents():
    classifier = IntentClassifier()
    classifier.add_intent("_nlp_my_command", ["do the thing", "trigger it"])
    classifier.retrain()

    NLPHandlerRegistry().register(NLPHandler(
        tool_name="_nlp_my_command",
        extract_fn=lambda text: {},
        response_fn=lambda _: "Done!",
        activity=[Activity.GENERAL],
    ))

# Auto-register at import time
register_my_intents()
```

The `NLPDispatcher` is available via `NLPHandlerRegistry().dispatcher` for accessing session state.

### Intent training tips

The IntentClassifier uses Naive Bayes with bag-of-words:

1. **10-15 examples per intent** is ideal. 4 is the minimum.
2. Use **distinctive words** — "weather forecast" is better than "what is the weather" (too generic).
3. Include **variations**: "set a timer", "start a countdown", "timer for 5 minutes".
4. **Avoid collisions** — if two tools share words like "next" (music skip vs cooking next step), rely on activity scoping to disambiguate.
5. Run `pytest tests/test_nlp.py -v` after adding intents to check for regressions.

## Display Views

Plugins can provide their own display views (full-screen pages and dashboard cards) without editing the display framework. Views are JavaScript modules loaded dynamically at runtime.

```python
# Register a view in __init__ or start()
self.register_view(
    view_type="my_view",                      # matches EventMessage name
    js_path="plugins/my_plugin/views/my.js",  # JS renderer module
    dashboard_card=True,                       # provides a dashboard card
)
```

The JS file registers on `GlaDOS.views` with `render(container, data)` and optionally `renderCard(container)`. See [Plugin Display Views](plugin-display.md) for the full guide, module contract, and CSS classes.

## UI Action Handlers

Plugins can register SocketIO event handlers so the display UI can call plugin functions directly — no LLM or NLP round-trip needed. This is the correct pattern for UI-driven actions (button clicks, form submissions, widget interactions).

### Registering a UI action

```python
class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        # Register the SocketIO event name and subscribe to handle it
        self.register_ui_action("my_plugin_action", self._on_ui_action)

    def start(self):
        self.event_system.subscribe(
            "ui.my_plugin_action",
            EventHook("my_ui_handler", callback=self._on_ui_action, priority=5)
        )

    def _on_ui_action(self, event: EventMessage):
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")
        if action == "do_something":
            result = self.do_something(data["param"])
            # Optionally speak result
            self.event_system.publish(EventMessage("tts", "speak", result["message"]))
```

### Frontend side

```javascript
// Direct action — no LLM, no NLP, instant
socket.emit('my_plugin_action', { action: 'do_something', param: 'value' });
```

The DisplayPlugin auto-discovers registered UI actions and creates SocketIO handlers dynamically. No changes to `display_server.py` needed.

### When to use UI actions vs. LLM tools

| Scenario | Use |
|----------|-----|
| Button click, form submit, widget interaction | `register_ui_action()` — direct, fast |
| Voice command with clear intent | NLP fast-path via `register_tool()` with intents |
| Ambiguous voice command needing interpretation | LLM tool via `register_tool()` with `process_output=True` |
| Background/periodic task | `system.tick` event subscription |

**Rule of thumb:** If the action comes from a UI element with known parameters, use `register_ui_action()`. If it comes from voice/text that needs interpretation, use `register_tool()`.

### Registered UI actions

| Event Name | Plugin | Actions |
|------------|--------|---------|
| `shopping_list_action` | PantryPlugin | toggle, remove, add_item, edit_item, set_recurring, complete, show, get_state, exit_mode |
| `pantry_action` | PantryPlugin | show, get_summary, remove_item, add_item, edit_item, set_expiry, add_location, remove_location, check_recipe, add_recipe_to_list |
| `recipe_action` | RecipeAPI | search, select, search_from_pantry |

## Chat Pipeline Hooks

Plugins can hook into the chat pipeline at specific phases to intercept, modify, or extend the conversation flow. This is how the memory system, hybrid NLP mode, and personality quips work — no core code modification needed.

### Phases

| Phase | When it runs | Use case |
|-------|-------------|----------|
| `PRE_LLM` | After activity classification, before LLM call | Memory retrieval, NLP fast-path, content filtering |
| `POST_RESPONSE` | After LLM response finalized, before EOS | Quip injection, logging, analytics |

### Registering a hook

```python
from glados.llm.chat_hooks import ChatPipelinePhase, ChatContext

class MyPlugin(RunnableMCPPlugin):
    def start(self):
        self.register_chat_hook(
            phase=ChatPipelinePhase.PRE_LLM,
            callback=self._my_hook,
            priority=10,  # lower = runs first
        )

    def _my_hook(self, ctx: ChatContext):
        # ctx.user_text — the user's input
        # ctx.activity — current Activity enum
        # ctx.tts_queue — inject speech directly
        # ctx.memory_context — set to inject context into LLM
        # ctx.handled = True — stops the pipeline (hook handled it)
        # ctx.extra — dict for passing data between hooks
        if "secret" in ctx.user_text:
            ctx.tts_queue.put("I know your secrets.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
```

### ChatContext fields

| Field | Type | Description |
|-------|------|-------------|
| `user_text` | str | The user's input text |
| `activity` | Activity | Current activity enum (GENERAL, COOKING, etc.) |
| `session_id` | str | Session UUID (persists across a single boot) |
| `tts_queue` | Queue | Inject speech directly (put text + `<EOS>`) |
| `memory_context` | str \| None | Set to inject context into the LLM call |
| `handled` | bool | Set True to stop the pipeline (hook already responded) |
| `extra` | dict | Pass data between hooks in the same phase |

### Built-in hooks

| Plugin | Phase | Priority | What it does |
|--------|-------|----------|-------------|
| MemoryCore | PRE_LLM | 10 | Detects remember/recall/forget/debug intents, auto-retrieves memory context |
| ConversationRAG | PRE_LLM | 12 | Retrieves relevant prior exchanges from Qdrant |
| KnowledgeRAG | PRE_LLM | 15 | Retrieves Wikipedia/knowledge passages from Qdrant |
| PersonalityCore | POST_RESPONSE | 50 | Injects contextual GLaDOS quips after responses |
| ConversationRAG | POST_RESPONSE | 50 | Stores the user+assistant exchange in Qdrant |

### All plugins

| Plugin | Type | Location | Description |
|--------|------|----------|-------------|
| **SarcasmCore** | System prompt | `plugins/cores/sarcasm_core.py` | GLaDOS personality via LLM prompt. Config: `enabled: true/false` |
| **ThreeLawsCore** | System prompt | `plugins/cores/three_laws_core.py` | Asimov's three laws of robotics. Config: `enabled: true/false` |
| **NeurotoxinEmittersCore** | System prompt | `plugins/cores/neurotoxin_emitters_core.py` | Adds lore about neurotoxin emitters being offline |
| **MemoryCore** | Chat hook | `plugins/cores/memory_core.py` | Memory remember/recall/forget/debug via PRE_LLM hook |
| **PersonalityCore** | Chat hook | `plugins/cores/personality_core.py` | Random contextual quips from `data/glados_quotes/` |
| **ConversationRAG** | Chat hook | `plugins/cores/conversation_rag.py` | Qdrant-backed conversation retrieval + storage |
| **KnowledgeRAG** | Chat hook + Tool | `plugins/knowledge/rag.py` | Passive RAG + `lookup_knowledge` active search tool |
| **LoopGuard** | Event monitor | `plugins/system/loop_guard.py` | Detects TTS repetition loops, interrupts |
| **CountdownTimer** | Tool | `plugins/basic/countdown_timer.py` | set_timer, list_timers, cancel_timer |
| **AlarmClock** | Tool | `plugins/basic/alarm_clock.py` | set_fixed_time_alarm, get_alarms, cancel_alarm |
| **MusicPlayer** | Tool | `plugins/music/music_player.py` | play_music, now_playing, list_devices |
| **DisplayPlugin** | Tool + Server | `plugins/display/display_server.py` | show_on_display, Flask+SocketIO web display |
| **RecipeAPI** | Tool | `plugins/recipes/recipe_api.py` | search_recipes, select_recipe, find_recipe_by_ingredients — 13.5K recipes with images, fuzzy ingredient matching |
| **CookingContext** | NLP-only | `plugins/recipes/cooking_context.py` | Step navigation (next/previous/repeat/ingredients) |
| **PantryPlugin** | Tool + Display | `plugins/pantry/pantry_plugin.py` | Shopping list, pantry inventory, expiry tracking, recipe integration (12 tools) |
| **LoggingPlugin** | Tool | `plugins/system/logging.py` | get_logs diagnostic tool |
| **LogAnalyzer** | Tool + Ring buffer | `plugins/system/log_analyzer.py` | Ring buffer log capture, `analyze_logs`, `save_log_report` — error analysis and JSON reports |
| **Observe** | Tool | `plugins/vision/observe.py` | get_camera_feed (POC) |

## Plugin Discovery

Plugins are auto-discovered from the `plugins/` directory on startup. Any `.py` file is imported, and any `RunnablePlugin` or `RunnableMCPPlugin` subclass found is instantiated and started automatically. Function plugins (`@mcp_tool`, `@plugin_manager.register`) register at import time.

NLP-only modules (like `cooking_context.py`) also auto-register their intents at import time — no explicit wiring needed in core code.
