# Configuration

All settings are in `glados_config.yml`.

## Core Settings

```yaml
Glados:
  completion_url: "http://localhost:1234/v1"   # LLM server endpoint
  model: "qwen/qwen3-30b-a3b-2507"            # model name
  client_type: OPENAI                           # OPENAI, ANTHROPIC, or LANGCHAIN
  api_key: "lm-studio"
```

## Speech & TTS

```yaml
  voice_core: "glados"            # "glados" (Piper/ONNX) or "kokoro" (Kokoro ONNX)
  voice_model: "glados.onnx"      # TTS model file or directory (in models/ dir)
  speaker_id: null                # speaker ID (int for Piper, string for Kokoro e.g. "bf_isabella")
  speech_buffer_ms: 1200          # ms of silence before finalizing speech
  tts_buffer_mode: "sentence"       # "sentence", "clause", or "word" (see below)
  tts_word_buffer: 5              # words per flush in "word" mode
  interrupt_on_wakeword: true     # say wake word while speaking to interrupt TTS
  hardware_echo_cancellation: false  # set true if speaker has hardware echo cancellation
  openwakeword:
    threshold: 0.5
    models:
      - models/glados_wakeword.onnx
```

### TTS Buffer Mode

Controls how quickly LLM output reaches the speaker. Smaller chunks = faster first audio, but may sound choppier with Piper.

| Mode | Splits on | Avg chunk | Latency | Quality |
|------|-----------|-----------|---------|---------|
| `sentence` | `.` `!` `?` | 15-25 words | 1-3s | Best prosody |
| `clause` (default) | `.` `!` `?` `,` `;` `:` `—` | 5-12 words | 0.5-1s | Good balance |
| `word` | Every N words | 4-6 words | 0.2-0.5s | Choppy with Piper, better with Kokoro |

Cooking abbreviations are expanded automatically for natural speech: Tbsp → tablespoon, tsp → teaspoon, oz → ounce, lb → pounds, etc.

### Voice Cores

GlaDOS supports switchable TTS backends via the `voice_core` config:

| Voice Core | Engine | Model | Notes |
|------------|--------|-------|-------|
| `glados` | Piper/ONNX | `glados.onnx` | Default. Classic GlaDOS voice. |
| `kokoro` | Kokoro ONNX | `kokoro-82m-onnx/` | Multiple voices via `speaker_id`. Requires `pip install kokoro-onnx`. |

#### Kokoro setup

1. `pip install kokoro-onnx`
2. Download model files (~340MB): `cd models/kokoro-82m-onnx && python get.py`
3. Set config:
   ```yaml
   voice_core: "kokoro"
   voice_model: "kokoro-82m-onnx"
   speaker_id: "bf_isabella"
   ```

#### Kokoro voices

Voice names use the format `{accent}{gender}_{name}`:
- Prefix: `a` = American, `b` = British
- Gender: `f` = female, `m` = male

| Voice | Description |
|-------|-------------|
| `af` | American female (default) |
| `af_bella` | American female, Bella |
| `af_nicole` | American female, Nicole |
| `af_sarah` | American female, Sarah |
| `af_sky` | American female, Sky |
| `am_adam` | American male, Adam |
| `am_michael` | American male, Michael |
| `bf_emma` | British female, Emma |
| `bf_isabella` | British female, Isabella |
| `bm_george` | British male, George |
| `bm_lewis` | British male, Lewis |

## Context & Routing

```yaml
  max_context_messages: 8           # max messages per activity context (keep small with RAG)
  plugin_intent_threshold: 0.5      # LLM forced tool-call threshold (1.0 to disable)
  hybrid_nlp_threshold: 0.8         # NLP fast-path threshold (1.0 to disable)
  thinking_enabled: false           # allow models to use think tags (slower)
  nlp_mode: false                   # true = pure NLP, no LLM
  nlp_confidence_threshold: 0.4     # minimum confidence for NLP dispatch
  max_response_tokens: 500          # max tokens per LLM text response (not tool calls)
  max_response_time: 15             # max seconds before aborting LLM stream
  max_tool_depth: 3                 # max recursive tool call depth (prevents loops)
  # Conversation RAG (Qdrant-backed conversation retrieval)
  conversation_rag_enabled: false
  conversation_rag_top_k: 5         # prior exchanges to retrieve per query
  conversation_rag_threshold: 0.4   # minimum similarity score
```

### Hybrid NLP+LLM (default)

When `hybrid_nlp_threshold < 1.0`, the IntentClassifier runs before the LLM on every request:
- **>= 0.8**: Tool executes via NLP instantly (~5ms), LLM only called if tool needs summarization
- **>= 0.5**: LLM runs with forced tool call (`tool_choice='required'`)
- **< 0.5**: LLM decides freely (`tool_choice='auto'`)

Set `hybrid_nlp_threshold: 1.0` to disable hybrid and use pure LLM mode.

#### Per-plugin NLP threshold

Individual plugins can set a lower confidence threshold so they take the NLP fast-path even when the global threshold is high. This is useful for well-defined tools (clock, timers) that suffer from confidence dilution as more plugins are added.

Set in code (default):
```python
@mcp_tool(..., nlp_threshold=0.6)
```

Override in config (takes priority):
```yaml
plugins:
  - name: get_current_time
    config:
      nlp_threshold: 0.5
```

Resolution order: **YAML config > code default > global `hybrid_nlp_threshold`**.

### Disabling forced tool selection

`plugin_intent_threshold` controls when the LLM is forced to call a specific tool (`tool_choice='required'`). When the IntentClassifier confidence exceeds this threshold, the LLM **must** call a tool rather than responding with text.

- `0.5` (default) — moderate forcing, good for most models
- `0.9` — only force on very obvious matches
- `1.0` — disable entirely, LLM always decides freely (`tool_choice='auto'`)

### Response safeguards

- `max_response_tokens` — caps LLM text output (not applied to tool calls or thinking models). Prevents runaway generation.
- `max_response_time` — wall-clock abort (starts counting after first visible token, excludes thinking time). If the LLM streams for longer than this, the response is cut off.
- `max_tool_depth` — prevents recursive tool call loops. The LLM can chain different tools (search→select→display) up to this depth. The same tool cannot be called twice in one exchange (repeat guard). Default 3.
- The **LoopGuard plugin** (`plugins/system/loop_guard.py`) monitors TTS for repeated sentences and interrupts automatically.

### Conversation RAG

When `conversation_rag_enabled: true`, exchanges are stored in Qdrant and semantically retrieved for future context. This replaces relying on the sliding window alone.

- **PRE_LLM hook** (priority 12): searches for relevant prior exchanges before each LLM call
- **POST_RESPONSE hook** (priority 50): stores the user+assistant exchange (spoken summary, not raw tool results)
- Persists across restarts — Qdrant collection survives reboots
- Works alongside ChromaDB memory (which handles explicit "remember that..." facts)

```yaml
  conversation_rag_enabled: true
  conversation_rag_top_k: 5        # prior exchanges to retrieve
  conversation_rag_threshold: 0.4  # minimum similarity score
```

Requires Qdrant running (same instance as knowledge RAG).

## Memory

```yaml
  memory_enabled: true              # persistent vector memory across sessions
  memory_auto_store: false          # auto-store every exchange (false = only explicit "remember that...")
  memory_db_path: "data/memory_db"  # ChromaDB file storage path
  memory_top_k: 5                   # past exchanges to retrieve per query
```

Memory operations are handled via a PRE_LLM chat pipeline hook — no LLM involvement needed.

- `memory_auto_store: true` — every user+assistant exchange is saved automatically (can get noisy)
- `memory_auto_store: false` (default) — only explicit "remember that..." facts are stored
- Relevant memories are always auto-retrieved and injected as context before each LLM call
- Say "dump memories" to log all stored memories to the console
- Say "forget everything" to clear all memories

The embedding model (`all-MiniLM-L6-v2`, ~90MB) is downloaded automatically on first use.

## Vision

```yaml
  vision_enabled: false
  vision_completion_url: "http://localhost:1234/v1"
  vision_model: "qwen/qwen3-vl-8b"
  vision_images_path: "vision_images"
```

## Knowledge Base (RAG)

Optional Qdrant-powered knowledge retrieval. Requires a running Qdrant server and ingested content.

```yaml
  knowledge_enabled: false                    # enable RAG retrieval
  qdrant_url: "http://localhost:6333"         # Qdrant server URL
  knowledge_collections:                      # collections to search
    - wikipedia
  knowledge_top_k: 3                          # passages per query
  knowledge_threshold: 0.5                    # minimum similarity (0.0-1.0)
  knowledge_embed_model: "all-MiniLM-L6-v2"  # embedding model
  knowledge_query_mode: "context"             # "raw", "context", or "rewrite"
  # knowledge_rewrite_model: "liquid/lfm2.5-1.2b"  # model for rewrite mode
  # knowledge_rewrite_url: null               # separate API endpoint for rewrite
```

When enabled, the KnowledgeRAG plugin registers a PRE_LLM hook that:
1. Builds a search query (mode-dependent: raw text, context-augmented, or LLM-rewritten)
2. Embeds the query and searches all configured Qdrant collections
3. Injects the top matching passages into the LLM context
4. Skips short commands (<10 chars) to avoid noise on tool invocations

See [Knowledge Base](knowledge.md) for details on query modes and benchmarks.

See [Knowledge Base](knowledge.md) wiki page for setup and ingestion instructions.

## Plugins

```yaml
  plugins:
    - name: music_player
      config:
        default_volume: 50
    - name: search_recipes
      config:
        max_results: 10
    - name: sarcasm_core
      config:
        enabled: true           # GLaDOS personality in LLM responses
    - name: three_laws_core
      config:
        enabled: true           # Three laws of robotics safety prompt
    - name: personality_core
      config:
        enabled: true           # Contextual quip injection after responses
        quip_chance: 0.15       # probability per response (0.0 to 1.0)
        cooldown_seconds: 120   # minimum gap between quips
        themes:                 # which quote themes to use
          - passive_aggressive
          - dark_humor
          - science
          - fake_empathy
          - food_cake
          - time_waiting
    - name: loop_guard
      config:
        buffer_size: 15         # sentences to track for repetition detection
```

### Personality Plugins

| Plugin | What it does | Config |
|--------|-------------|--------|
| **SarcasmCore** | Injects GLaDOS personality into the LLM system prompt | `enabled: true/false` |
| **PersonalityCore** | Appends contextual quips from `data/glados_quotes/` after responses | `quip_chance`, `cooldown_seconds`, `themes` |
| **ThreeLawsCore** | Adds Asimov's three laws of robotics to the system prompt | `enabled: true/false` |
| **LoopGuard** | Monitors TTS for repeated sentences and interrupts | `buffer_size` |
| **LogAnalyzer** | Ring buffer log capture, error analysis, saved reports | `buffer_size` |

### Log Analyzer

```yaml
  plugins:
    - name: log_analyzer
      config:
        buffer_size: 5000   # max log entries in ring buffer (default: 5000)
```

All loguru logs (DEBUG and above) are captured into a ring buffer. Ask "check the logs" or "any errors?" to get a summary, or "save a log report" to dump a structured JSON report to `plugin_data/log_analyzer/`.

## External MCP Servers

```yaml
  mcp_servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

## LLM Backends

GlaDOS supports multiple LLM backends via the `client_type` config:

| Backend | Config | Description |
|---------|--------|-------------|
| `OPENAI` | `completion_url` + `api_key` | OpenAI-compatible API (LM Studio, Ollama via OpenAI API, actual OpenAI) |
| `ANTHROPIC` | `api_key` | Claude API (Anthropic). No `completion_url` needed. |
| `LANGCHAIN` | `completion_url` | LangChain/Ollama native integration |

### Using Claude

```yaml
  client_type: ANTHROPIC
  model: "claude-sonnet-4-20250514"
  api_key: "sk-ant-api03-..."
```

Requires: `pip install anthropic`

### Using a local model (LM Studio / Ollama)

```yaml
  client_type: OPENAI
  completion_url: "http://localhost:1234/v1"
  model: "qwen2.5-7b-instruct"
  api_key: "lm-studio"
```

Each backend is in `glados/llm/backends/`. Adding a new provider = one file extending `LLMBackend`.

## Pantry & Shopping List

```yaml
  normalize_shopping_items: true    # normalize ingredient names to match recipe data (default: true)
  recipe_qdrant_enabled: false      # enable Qdrant-backed semantic recipe search (default: false)
  recipe_classify_model: "google/gemma-3n-e4b"  # model for recipe categorization + ingredient extraction (null = main model)
  metric_annotations: true              # annotate imperial measurements with metric equivalents in recipes

  plugins:
    - name: pantry_plugin
      config:
        shopping_mode_timeout: 60   # seconds of inactivity before auto-exiting planning/post-shopping mode
```

### Ingredient Normalization

When `normalize_shopping_items: true` (default), items added to the shopping list are matched against known recipe ingredient names. "Chicken" becomes "chicken breast", "butter" stays "butter" (already a core name). This improves recipe-matching accuracy significantly.

- **Voice**: GlaDOS confirms the normalized name ("Added chicken breast"). Say "that's wrong" or "use the name I said" to revert.
- **Display UI**: Autocomplete suggestions appear as you type, showing matching ingredient names from the recipe database.
- **Graceful degradation**: If the ingredient map hasn't been generated yet, items are stored verbatim.

Set `normalize_shopping_items: false` to disable all normalization. Autocomplete suggestions still appear in the UI.

See [Shopping List & Pantry — Ingredient Normalization](pantry.md#ingredient-normalization) for setup and backfill instructions.

### Semantic Recipe Search (Qdrant)

When `recipe_qdrant_enabled: true`, recipes are embedded into a Qdrant collection (`recipe_ingredients`) for semantic ingredient matching. "What can I make with chicken and rice" uses vector similarity instead of keyword matching.

- Requires Qdrant running (same instance as knowledge RAG, at `qdrant_url`)
- Collection auto-builds on first startup (~2 min for 13.5K recipes)
- Falls back to fuzzy matching if Qdrant is unavailable
- The recipe search UI shows which method was used ("semantic search" or "basic matching")

Build the collection offline (faster): `python tools/ingest_recipes_qdrant.py`

No other config needed — pantry works out of the box with default storage locations. Locations are managed via voice or the display UI.

## Logging

Logs are colorized by subsystem for easy scanning:

| Color | Subsystem | Modules |
|-------|-----------|---------|
| Cyan/Blue | Plugins | plugin registration, pantry, recipes, music, etc. |
| Yellow | LLM Pipeline | chat_client, stream_handler, response_processor, tool_executor |
| Magenta | NLP | dispatcher, intent classifier, NLP handlers |
| Green | Voice/Audio | speech_module, whisper, wake word detection |
| White | Display/Events | display_server, event_system |

Log level defaults to INFO. Configured in `main.py:17-18`.

## Other

```yaml
  music_dir: ~/Music              # music player directory
  display_port: 5001              # display UI port
  interruptible: false            # deprecated, use interrupt_on_wakeword
```
