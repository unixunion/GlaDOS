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
  speech_buffer_ms: 1200          # ms of silence before finalizing speech
  interrupt_on_wakeword: true     # say wake word while speaking to interrupt TTS
  hardware_echo_cancellation: false  # set true if speaker has hardware echo cancellation
  voice_model: "glados.onnx"      # TTS voice model file (in models/ dir)
  speaker_id: null                # TTS speaker ID (model-specific)
  openwakeword:
    threshold: 0.5
    models:
      - models/glados_wakeword.onnx
```

## Context

```yaml
  max_context_messages: 20        # max messages per activity context
  plugin_intent_threshold: 0.7    # confidence threshold for intent classifier
  thinking_enabled: false         # allow models to use think tags (slower)
```

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
