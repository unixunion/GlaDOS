# Configuration

All settings are in `glados_config.yml`.

## Core Settings

```yaml
Glados:
  completion_url: "http://localhost:1234/v1"   # LLM server endpoint
  model: "qwen/qwen3-30b-a3b-2507"            # model name
  client_type: OPENAI                           # OPENAI, LANGCHAIN, or MISTRAL
  api_key: "lm-studio"
```

## Speech & TTS

```yaml
  voice_core: "glados"            # "glados" (Piper/ONNX) or "kokoro" (Kokoro ONNX)
  voice_model: "glados.onnx"      # TTS model file or directory (in models/ dir)
  speaker_id: null                # speaker ID (int for Piper, string for Kokoro e.g. "bf_isabella")
  speech_buffer_ms: 1200          # ms of silence before finalizing speech
  interrupt_on_wakeword: true     # say wake word while speaking to interrupt TTS
  hardware_echo_cancellation: false  # set true if speaker has hardware echo cancellation
  openwakeword:
    threshold: 0.5
    models:
      - models/glados_wakeword.onnx
```

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

## Context

```yaml
  max_context_messages: 20        # max messages per activity context
  plugin_intent_threshold: 0.7    # confidence threshold for intent classifier
  thinking_enabled: false         # allow models to use think tags (slower)
```

## Memory

```yaml
  memory_enabled: true              # persistent vector memory across sessions
  memory_db_path: "data/memory_db"  # ChromaDB file storage path
  memory_top_k: 5                   # past exchanges to retrieve per query
```

When enabled, each user+assistant exchange is stored in ChromaDB and the most relevant past exchanges are injected as context before each LLM call. Explicit "remember that..." and "do you remember..." requests are detected by the IntentClassifier and handled pre-LLM — no tool calls needed.

The embedding model (`all-MiniLM-L6-v2`, ~90MB) is downloaded automatically on first use.

## Vision

```yaml
  vision_enabled: false
  vision_completion_url: "http://localhost:1234/v1"
  vision_model: "qwen/qwen3-vl-8b"
  vision_images_path: "vision_images"
```

## Plugins

```yaml
  plugins:
    - name: music_player
      config:
        default_volume: 50
    - name: search_recipes
      config:
        max_results: 10
```

## External MCP Servers

```yaml
  mcp_servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

## Other

```yaml
  music_dir: ~/Music              # music player directory
  display_port: 5001              # display UI port
  interruptible: false            # deprecated, use interrupt_on_wakeword
```
