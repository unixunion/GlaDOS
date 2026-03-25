# NLP Mode & Hybrid Mode

## Hybrid Mode (Default) — Fast NLP + LLM Fallback

Hybrid mode gives you the best of both worlds: the NLP classifier runs a fast pre-check (~5ms) on every user input. If it's highly confident, the tool executes immediately without waiting for the LLM. Ambiguous requests still get full LLM reasoning.

### Three-Tier Routing

| Confidence | Path | Speed | Example |
|-----------|------|-------|---------|
| >= 0.8 (high) | **NLP direct** — tool executes immediately, response spoken via template | ~5ms | "set a timer for 5 minutes" |
| >= 0.5 (medium) | **LLM forced** — LLM runs with `tool_choice='required'` | ~500ms | "play something relaxing" |
| < 0.5 (low) | **LLM auto** — LLM decides whether to call a tool | ~1-3s | "I'm hungry, what should I make" |

When a tool executed via NLP has `process_output=True` (e.g., recipe search returns JSON), only the summarization step goes through the LLM — the tool itself executed instantly.

### Configuration

```yaml
hybrid_nlp_threshold: 0.8    # NLP fast-path (set to 1.0 to disable hybrid, pure LLM)
plugin_intent_threshold: 0.5  # LLM forced tool-call threshold
```

### How It Works

```
User Input → ChatClient.chat()
  ├─ Step 1: infer_activity (IntentClassifier)
  ├─ Step 2: add user message to history
  ├─ Step 3: memory processing (remember/recall/forget — pre-LLM)
  │
  ├─ Step 3.5 [Hybrid]: IntentClassifier.predict_intent(text)
  │     confidence >= hybrid_nlp_threshold?
  │       YES, tool has process_output=False:
  │         → NLP executes tool directly, speaks response, DONE
  │       YES, tool has process_output=True:
  │         → NLP executes tool, injects result, LLM summarizes
  │       NO: fall through to LLM
  │
  ├─ Step 4 [LLM]: StreamHandler.stream_response()
  │     IntentClassifier confidence >= plugin_intent_threshold?
  │       YES → tool_choice='required' (LLM must call the predicted tool)
  │       NO  → tool_choice='auto' (LLM decides)
  │
  └─ Step 5: ResponseProcessor streams text to TTS
```

---

## Pure NLP Mode — LLM-Free Operation

NLP mode lets GlaDOS run without an external LLM server. Instead of sending user input to an LLM, it uses the IntentClassifier (Naive Bayes) to identify which tool to call, extracts parameters via regex, calls the tool directly, and speaks a template response via TTS.

This makes GlaDOS usable on low-power devices (Raspberry Pi, old laptops) where running or connecting to an LLM isn't practical.

### Enabling NLP Mode

In `glados_config.yml`:

```yaml
nlp_mode: true
nlp_confidence_threshold: 0.4  # minimum classifier confidence to call a tool
```

Then run as usual:

```bash
# Full voice mode (mic + TTS, no LLM needed)
python main.py

# Text input with TTS output
python main.py --text

# Pure text mode (no audio hardware)
python main.py --no-speech
```

When `nlp_mode: true`:
- No LLM server connection is attempted
- Model discovery is skipped
- The startup announcement speaks directly ("System online. NLP mode active.")
- All user input is routed through the NLP dispatcher instead of the LLM
- Hybrid mode is not used (NLP handles everything)

The memory system still works in NLP mode — "remember that I prefer celsius" stores facts, "what do you remember" retrieves them.

## Architecture

NLP mode core lives in `glados/nlp/`:

| File | Purpose |
|------|---------|
| `handler.py` | `NLPHandler` dataclass + `NLPHandlerRegistry` singleton (also holds dispatcher reference) |
| `dispatcher.py` | `NLPDispatcher` — classifies, extracts, calls, speaks. Registers itself on `NLPHandlerRegistry` at construction. |
| `extractors.py` | Shared utilities: duration parsing, number words, music actions |

Plugin-side NLP extensions live alongside their plugins:

| File | Purpose |
|------|---------|
| `plugins/recipes/cooking_context.py` | Cooking session commands (ingredients, steps, navigation). Auto-registers at import. |

## Adding NLP Support to a Plugin

NLP support is opt-in per tool. Tools without NLP handlers still work — they're attempted as parameterless calls, or the dispatcher says "I couldn't extract the details."

### Simple Function Plugin (`@mcp_tool`)

For **parameterless tools**, just add `nlp_response`:

```python
@mcp_tool(
    description="Get current time",
    intents=["what is the time", "what time is it"],
    activity=[Activity.GENERAL],
    nlp_response=lambda r: f"The time is {json.loads(r)['time']}.",
)
def get_current_time() -> str:
    return json.dumps({"time": datetime.now().strftime("%H:%M:%S")})
```

For **tools with parameters**, add `nlp_extractors` (regex-based) or `nlp_extract_fn` (function-based):

**Regex extractors** — each key maps to a parameter, patterns use named groups:

```python
import re

@mcp_tool(
    description="Get weather for a location.",
    parameters={"location": {"type": "string", "description": "City name"}},
    required=["location"],
    intents=["what is the weather", "weather forecast"],
    nlp_extractors={
        "location": [
            re.compile(r"\b(?:in|for|at)\s+(?P<location>.+?)$", re.IGNORECASE),
        ],
    },
    nlp_response=lambda r: f"The weather is {r}.",
)
def handle_weather(location: str) -> str:
    return "Sunny, 25C"
```

**Function extractors** — for complex parsing logic:

```python
from glados.nlp.extractors import parse_duration, extract_after_keyword

def _timer_extract(text: str) -> dict:
    params = {}
    duration = parse_duration(text)
    if duration:
        params.update(duration)  # adds hours, minutes, seconds
    desc = extract_after_keyword(text, ["called", "named"])
    if desc and not parse_duration(desc):
        params["description"] = desc
    return params

def _timer_response(result: dict) -> str:
    if result.get("status") == "error":
        return result["message"]
    return f"{result['message']} It will go off at {result['expires_at']}."
```

Then in the registration:

```python
self.register_tool(
    handler=self.set_timer,
    description="Set a timer",
    parameters={...},
    intents=["set a timer for twelve minutes", ...],
    nlp_extract_fn=_timer_extract,
    nlp_response=_timer_response,
)
```

### Stateful Plugin (`RunnableMCPPlugin`)

Same parameters available on `self.register_tool()`:

```python
self.register_tool(
    handler=self.play_music,
    description="Controls music playback",
    parameters={...},
    intents=[...],
    nlp_extract_fn=_music_nlp_extract,    # text -> {"action": "PLAY", "query": "..."}
    nlp_response=_music_nlp_response,     # result -> "Now playing X by Y."
)
```

### Legacy plugins (`@plugin_manager.register`)

Register an `NLPHandler` directly:

```python
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry

NLPHandlerRegistry().register(NLPHandler(
    tool_name="search_recipes",
    extract_fn=lambda text: {"query": text},
    response_fn=lambda r: "I found some recipes.",
))
```

## Registration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `nlp_extractors` | `dict[str, list]` | Param name → list of regex patterns with named groups |
| `nlp_response` | `Callable[[Any], str]` | Formats tool result as spoken text |
| `nlp_extract_fn` | `Callable[[str], dict]` | Custom function: user text → tool kwargs (overrides regex) |

All three are optional. If none are provided, the tool has no NLP handler and will be attempted as a parameterless call.

## Shared Extractors

`glados/nlp/extractors.py` provides reusable parsing utilities:

| Function | Description | Example |
|----------|-------------|---------|
| `parse_duration(text)` | Extracts hours/minutes/seconds | `"5 minutes and 30 seconds"` → `{"hours": 0, "minutes": 5, "seconds": 30}` |
| `word_to_number(text)` | Converts word to int (0-99) | `"twelve"` → `12`, `"twenty five"` → `25` |
| `extract_after_keyword(text, keywords)` | Text after a keyword | `"timer for eggs"` with `["for"]` → `"eggs"` |
| `extract_music_action(text)` | Music action + query | `"play bohemian rhapsody"` → `("PLAY", "bohemian rhapsody")` |

## Intent Training Tips

The IntentClassifier uses Naive Bayes with bag-of-words. Keep these in mind:

1. **More examples = better accuracy.** 4 examples is the minimum; 10-15 is ideal.
2. **Avoid generic phrases** like "what is" — they overlap with many tools. Use distinctive words: "weather forecast" not just "what is the weather."
3. **Include variations**: "set a timer", "start a countdown", "timer for X minutes."
4. **Run the scorecard** after adding intents to check for regressions.

## Testing

### Run the full NLP test suite

```bash
pytest tests/test_nlp.py -v
```

This runs:
- **Extractor unit tests** — `word_to_number`, `parse_duration`, `extract_music_action`, etc.
- **Handler tests** — regex extraction, function extraction, response formatting
- **Intent classification tests** — golden set of phrases that must route correctly
- **Handler registration tests** — verifies all expected plugins registered NLP handlers
- **Parameter extraction integration** — tests real plugin extractors with realistic input
- **End-to-end dispatch** — classifies → extracts → calls tool → checks TTS output
- **Scorecard** — prints per-tool accuracy breakdown

### Run only the scorecard

```bash
python tests/test_nlp.py
```

Outputs a table showing every test phrase, what it classified as, and per-tool accuracy:

```
  NLP Intent Classification — Full Scorecard

  [ ok ]    what is the time             -> get_current_time   (0.895)
  [ ok ]    set a timer for 12 minutes   -> set_timer          (0.782)
  [MISS]    list all timers              -> list_plugins       (0.470)
  ...

  Per-tool accuracy:
    get_current_time               [#####] 5/5
    set_timer                      [###] 3/3
    list_plugins                   [##] 2/2
```

### Adding test cases

Add entries to `INTENT_TEST_CASES` in `tests/test_nlp.py`:

```python
INTENT_TEST_CASES = [
    # (input_text, expected_tool_name, min_confidence)
    ("what is the time", "get_current_time", 0.5),
    ("your new phrase here", "your_tool_name", 0.3),
    ...
]
```

## Activity-Scoped Dispatch

NLP mode uses **two-layer classification** to route commands based on the current activity context:

```
NLPDispatcher.dispatch(text, current_activity)
  │
  ├─ Layer 1: Activity-scoped classification
  │    Get tool names for current_activity (from PluginSystem + NLPHandlerRegistry)
  │    Filter classifier probabilities to only those tools
  │    If best match >= threshold → use it
  │
  ├─ Layer 2: Global fallback (only if Layer 1 fails)
  │    Run normal global classification
  │    If matched tool belongs to a different activity → still works
  │
  └─ Both fail → "I didn't understand that"
```

This means: in COOKING mode, "list ingredients" matches cooking tools first, but "what time is it" still works because the global fallback catches it.

### Scoped classification method

`IntentClassifier.predict_intent_scoped(text, tool_names)` filters the probability vector to a subset of tool names and returns the best match from that set.

## Cooking Context

After selecting a recipe (`select_recipe`), the dispatcher stores the result in a per-activity session. Cooking-context NLP commands read from this session to answer questions about the selected recipe.

### Session state

```python
dispatcher._session[Activity.COOKING] = {
    "selected_recipe": { ... },   # Full recipe result from select_recipe
    "current_step": 0,            # Tracks position in step-by-step mode
}
```

### Cooking commands

| Command | Example phrases | Behavior |
|---------|----------------|----------|
| `_nlp_list_ingredients` | "list the ingredients", "ingredients", "what do I need" | Reads ingredients from session recipe |
| `_nlp_list_steps` | "what are the steps", "steps", "go step by step" | Lists all steps |
| `_nlp_next_step` | "next step", "what's next", "continue", "keep going" | Reads current step, advances counter |
| `_nlp_previous_step` | "previous step", "go back", "take me back" | Goes back one step |
| `_nlp_first_step` | "first step", "start from the beginning", "from the top" | Resets to step 1 |
| `_nlp_repeat_step` | "repeat that", "say that again", "again" | Re-reads current step |
| `_nlp_current_recipe` | "what are we making", "which recipe" | Says the recipe title |

### Example flow

1. "find me a recipe for pecan pralines" → searches recipes, reads top matches
2. "lets make pecan pralines" → selects recipe, stores session, displays on screen
3. "list the ingredients" → reads ingredients (scoped to COOKING)
4. "next step" → reads step 1
5. "next step" → reads step 2
6. "repeat that" → re-reads step 2
7. "go back" → re-reads step 1
8. "first step" → resets to step 1
9. "what time is it" → still works (global fallback)

### Adding cooking commands

Cooking commands are registered in `plugins/recipes/cooking_context.py`. They auto-register when the module is imported during `load_plugins()`. Each command needs:
- Intent examples (registered with IntentClassifier)
- A response function that reads from `_get_cooking_session()` (looks up the dispatcher via `NLPHandlerRegistry().dispatcher`)
- Registration as an `NLPHandler` with `activity=[Activity.COOKING]`

### Dispatcher wiring

The `NLPDispatcher` registers itself on `NLPHandlerRegistry().dispatcher` during construction. Plugins that need session access (like cooking context) look it up lazily from the registry — no explicit import or `set_dispatcher()` call needed. This keeps plugin discovery fully dynamic.

## Available NLP Tools

All plugins with NLP support (as of the current build):

| Tool | Plugin | Extraction | Notes |
|------|--------|-----------|-------|
| `get_current_time` | clock.py | None (no params) | |
| `handle_weather` | weather.py | Regex (location) | Uses Open-Meteo API |
| `set_timer` | countdown_timer.py | Custom fn (duration parsing) | |
| `list_timers` | countdown_timer.py | None | |
| `set_fixed_time_alarm` | alarm_clock.py | Custom fn (time parsing) | |
| `get_alarms` | alarm_clock.py | None | |
| `cancel_alarm` | alarm_clock.py | Custom fn | |
| `play_music` | music_player.py | Custom fn (action + query) | Spotify API |
| `now_playing` | music_player.py | None | |
| `list_devices` | music_player.py | None | |
| `search_recipes` | recipe_api.py | Custom fn (query stripping) | |
| `select_recipe` | recipe_api.py | Custom fn (query stripping) | |
| `convert_units` | unit_converter.py | Custom fn (value + units) | Temp, weight, volume, length |
| `add_two_numbers` | basic_math.py | Custom fn (number parsing) | |
| `subtract_two_numbers` | basic_math.py | Custom fn (number parsing) | |
| `list_plugins` | helpers.py | None | |
| `get_logs` | logging.py | None | |
| `start_vacuuming` | robot_vacuum.py | None | Stub implementation |
| `stop_vacuuming` | robot_vacuum.py | None | Stub implementation |
| Cooking context (7) | cooking_context.py | None (session-based) | Steps, ingredients, navigation |

## Limitations

- **Naive Bayes classifier** — simple bag-of-words; ambiguous phrases like "what is..." can confuse it. More intent examples improve accuracy.
- **Cross-domain word collisions** — words shared between intents (e.g. "next" in music skip vs cooking next step) can cause misrouting in global classification. Activity scoping mitigates this at runtime.
- **Regex extraction** — complex parameter shapes (nested objects, free-form text) need custom `extract_fn`.
- **No fallback to LLM** — if the classifier is wrong, there's no recovery. The threshold helps but isn't perfect.
- **Template responses** — responses are formatted by code, not generated. They're functional but not conversational.
- **Cooking session is ephemeral** — restarting GlaDOS clears the selected recipe and step progress.
