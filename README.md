# GlaDOS, a maniacal home assistant

This fork introduces a pluggable architecture, with function calling support to for GLaDOS.
WARNING! GLaDOS is maniacal, and ultimately evil, so be careful in connecting her to any real world stuff.
you have been warned! Although you should be ok as long as you use a "safe" llm, and a copy of the laws of robotics.

This is under development right now, and lots of stuff is in a state of flux.

# models to test

- https://huggingface.co/lmstudio-community/Qwen2.5-32B-Instruct-GGUF   

## LLM Model Guide

GlaDOS uses tool/function calling to interact with plugins (15-30+ tools). This puts specific demands on the LLM:

- **Tool calling support** — the model must reliably generate structured function calls, not just chat
- **Low latency** — voice assistant needs fast responses, so smaller quantized models on local hardware win over cloud
- **Brief responses** — models that ramble waste TTS time; instruction-following models that respect "be concise" prompts work best
- **Context window** — each tool definition is ~100-200 tokens of JSON schema, so 20 tools eat ~2,000-4,000 tokens before any conversation

### What to look for

- Models fine-tuned for **function/tool calling** (not just chat)
- Good **instruction following** — respects system prompt constraints like brevity
- Adequate **context window** (8K minimum, 32K+ preferred with many tools)
- Quantization: Q4_K_M is the sweet spot for quality vs. speed on consumer hardware

### Recommended Models by Hardware Tier

| VRAM / Unified Memory | Recommended Models | Notes |
|----------------------|-------------------|-------|
| **8GB** | Qwen 2.5 3B, Llama 3.2 3B | Minimal tool calling ability, fine for basic testing |
| **16GB** | Qwen 2.5 7B, Qwen 3 8B, Llama 3.1 8B | Workable with <15 tools, struggles with more |
| **32GB** | Qwen 2.5 32B (Q4), Mistral Small 24B (Q4) | Good tool calling, handles 20+ tools well |
| **64GB+** | Qwen 2.5 72B (Q4), Llama 3.1 70B (Q4) | Excellent tool calling, 50+ tools reliable |
| **NVIDIA 24GB** (RTX 4090) | Qwen 2.5 32B (Q4) | Fits in single GPU |
| **NVIDIA 48GB** (2x 3090, A6000) | Qwen 2.5 72B (Q4), Llama 3.1 70B (Q4) | Best open-source tool calling |

### Models Known to Work

Benchmarked with `tests/benchmark_models.py` (58 single-turn + 11 multi-turn chain tests):

| Model | Accuracy | Avg TTFT | Notes |
|-------|----------|----------|-------|
| Qwen 2.5 32B Instruct | 94.2% | 2.5s | Best accuracy, reliable tool calling |
| Google Gemma 3 12B | 91.4% | 20s | Very accurate but slow |
| Qwen 3 Coder 30B (MoE) | 89.7% | 0.5s | Best speed/accuracy tradeoff |
| Mistral Magistral Small | 89.9% | 1.4s | Strong all-rounder |
| Qwen 3 30B-A3B (MoE) | 86.2% | 0.5s | Fastest, ~3B active params |
| OpenAI GPT-OSS 20B | 81.0% | 3.7s | Decent but slow |

Run your own benchmarks: `python tests/benchmark_models.py --all` (cycles through all installed models) or `python tests/benchmark_models.py --report` to view saved results.

### Known Issues

- **Small models (<7B)** often output tool calls as raw text instead of structured JSON, causing TTS to read out function definitions
- **Qwen 3.x thinking models** may narrate their reasoning process out loud — the response processor strips common patterns but some leak through
- **Models not trained for function calling** will ignore tool definitions entirely and just chat

### Servers

GlaDOS works with any OpenAI-compatible API server:
- **LM Studio** — easy GUI, OpenAI-compatible endpoint at `http://localhost:1234/v1`
- **Ollama** — CLI-based, endpoint at `http://localhost:11434/v1`
- **Any cloud API** — set `completion_url` and `api_key` in `glados_config.yml`

## Architecture

This is pretty much a total re-write of the upstream project, using a more modular approach. Features:

* Pre-prompted with the 3 laws of robotics
* Plugin support, and long-running processes instantiation of classes base: `RunnablePlugin`
* Functions, GladOS can now interact with stuff ( using llama 3.1 functions (read more)[https://docs.together.ai/docs/function-calling] )
  The functions can take arguments, enums and then do whatever you integrate them with. WARNING! Remember the bitch is evil!
  These functions register as a either standalone or as a part of plugin instances.
* Intents, to help guide the AI to select a function/tool, the plugins have "intent" strings that are used to help select the relevant tool.
* Event system, plugins, functions and parts of the architecture all use events now to talk each other and the LLM.
* Vision support, yes, its probably a bad idea, but GlaDOS can see! well its very basic POC, images can be base64 encoded and passed to a
  vision model, which in turn responds to the chat model. And GlaDOS can trigger functions automatically then, like
  start vacuuming if there is a floor spill, or start fire supression if there is a fire, or she may just watch you burn.
  I'm using a separate host to run the vision model, and calling it over the network. A camera system needs to be implemented
  to get images from CCTV or similar.
* Migrated to Whisper for speech to text

## Persistent Memory

GlaDOS has persistent vector memory powered by ChromaDB. Past conversations are stored as embedded documents and semantically retrieved before each LLM call, so GlaDOS can recall relevant context from prior sessions.

- **Automatic storage** — each user+assistant exchange is stored after every LLM response
- **Automatic retrieval** — top-k most relevant past exchanges (+ explicit facts) are injected as context before each LLM call
- **Pre-LLM interception** — "remember that..." and "do you remember..." are detected by the IntentClassifier (same Naive Bayes classifier used for tool routing) and handled deterministically by the application layer before the LLM runs. This works reliably with any model size — no tool-calling capability required.
- **Persistent** — memory survives restarts, stored in `data/memory_db/` via ChromaDB's file-based PersistentClient
- **Semantic search** — uses `all-MiniLM-L6-v2` embeddings (~90MB, downloads on first use) with cosine similarity
- **Dual retrieval** — queries merge activity-filtered conversation history with explicit facts, so stored preferences are always retrievable regardless of activity context

Configure in `glados_config.yml`:
```yaml
memory_enabled: true       # false to disable entirely
memory_db_path: "data/memory_db"
memory_top_k: 5            # number of past exchanges to retrieve
```

## Hybrid NLP+LLM Mode

By default, GlaDOS uses a hybrid approach for faster responses. The IntentClassifier runs a fast pre-check (~5ms) on every user input:

- **High confidence** (>= 0.8): Tool is executed immediately via NLP — no LLM call needed. If the tool needs a natural spoken summary (`process_output=True`), only the summarization goes through the LLM.
- **Medium confidence** (>= 0.5): LLM runs with `tool_choice='required'`, forcing it to call the predicted tool.
- **Low confidence**: LLM runs normally with `tool_choice='auto'`.

This means clear commands like "set a timer for 5 minutes" or "what time is it" execute instantly, while ambiguous requests still get full LLM reasoning.

Configure in `glados_config.yml`:
```yaml
hybrid_nlp_threshold: 0.8   # NLP fast-path threshold (set to 1.0 to disable)
plugin_intent_threshold: 0.5 # LLM forced tool-call threshold
```

## Activity System

The activity system provides separate message contexts per activity (COOKING, CHORES, UTILITIES, SYSTEM, ENTERTAINMENT, GENERAL) with tool filtering so the LLM only sees relevant tools for the current context.

**Flow:** User Input → IntentClassifier predicts tool → tool's activity → switch_context → filter tools → LLM call

Available activities and typical tools:

| Activity | Tools |
|----------|-------|
| GENERAL | weather, time, recipes, display, alarms, memory tools |
| COOKING | recipes, timers, alarms, display, time |
| UTILITIES | weather, timers, alarms, display, time |
| CHORES | vacuum, display |
| SYSTEM | time, logs, list_plugins, memory tools |
| ENTERTAINMENT | music player |

The system prompt is shared across all activity contexts. The display UI shows the current activity as a pill icon in the top-left corner.

## Wake Word

Using OpenWakeWord for wake word detection. The default model responds to "GlaDOS" and "Hey GlaDOS".

Configure in `glados_config.yml`:
```yaml
openwakeword:
  threshold: 0.5
  models:
    - models/glados_wakeword.onnx  # responds to "GlaDOS" and "Hey GlaDOS"
```

A confirmation beep plays when the system starts listening after a wake word, so you know when to speak. The beep also plays when the system auto-listens for a follow-up response after TTS finishes.

### Mute / Unmute

Say wake word + one of these to mute:
- "stop listening", "go to sleep", "mute yourself", "be quiet"

Say wake word + one of these to unmute:
- "start listening", "resume listening", "wake up", "unmute"

When muted, the wake word still fires but only listens briefly for an unmute command. All other input is dropped.

## Functions

### Timers

- `"set a timer for 5 minutes"` — starts a countdown timer, fires an event + audio alert when done
- `"list timers"` — shows active timers and time remaining
- Timer alerts flash the display screen and play a sound

### Alarms

- `"set an alarm for 5pm tomorrow"` — sets an alarm using natural language time (powered by `dateparser`)
- `"list my alarms"` — shows all pending alarms
- `"cancel the morning alarm"` — cancels by description or time
- When an alarm fires, it plays a repeating audio alert (2s gap between cycles) until dismissed
- Alarm pauses any playing music and resumes it after dismissal
- Dismiss by saying wake word + "stop", "cancel", "silence", "dismiss", etc.
- Uses `PREFER_DATES_FROM: future` so "5pm" rolls to tomorrow if already past

### Recipes

Get a recipe csv [recipes dataset](https://www.kaggle.com/datasets/wilmerarltstrmberg/recipe-dataset-over-2m) and place it in `plugin_data/recipes/dataset.csv`,
then you can use it e.g: `select a recipe for x` - selects a recipe to make or `search for a recipe for y` to get a list
of options after which you will use the _select recipe x_ statement to make it.

When a recipe is selected, it is automatically pushed to the display screen (see Display below).

### Music Player

The music player scans a configurable directory for audio files (mp3, m4a, flac, wav, ogg, aac) and uses fuzzy matching to find songs.

Configure the music directory in `glados_config.yml`:
```yaml
music_dir: ~/Music
```
Or set the `GLADOS_MUSIC_DIR` environment variable.

Voice commands:
- `"play ben howard"` — fuzzy matches and plays the best match
- `"stop the music"` / `"pause"` / `"resume"` — playback controls
- `"what song is playing"` — shows current track

Playback runs in a background thread and doesn't block the LLM. Music is automatically paused when an alarm fires and resumed after dismissal. Saying "stop" while music is playing (and no alarm is ringing) stops the music directly without going through the LLM.

### Display

The display plugin serves a web-based display intended for a kitchen iPad or any browser. It runs a Flask+SocketIO server on port 5001.

- Open `http://<host>:5001` on an iPad or browser to see the display
- Shows an idle clock view by default with the GlaDOS avatar in the top-right
- **Activity indicator** — shows the current activity context (cooking, utilities, chores, system) as an icon pill in the top-left
- **Status toast** — bottom-center toast shows system state: listening (green), thinking (orange), speaking (orange-red), tool call (blue), idle (grey)
- Voice commands push content to the screen via the `show_on_display` LLM tool:
  - `"show me the recipe"` — displays the current recipe with ingredients and steps
  - `"display the timer"` — shows a live countdown of active timers
  - `"clear the screen"` — returns to the idle clock view
  - `"show that on the iPad"` — general display command for any content
- Recipes are automatically displayed when selected (no separate voice command needed)
- When a timer or alarm fires, the display flashes an alert

### Voice Commands (intercepted before LLM)

These commands are handled directly by the speech system without going through the LLM:

| Command | Action |
|---------|--------|
| "stop" / "cancel" / "silence" / "dismiss" | Dismiss ringing alarm, or stop music |
| "stop listening" / "go to sleep" | Mute — ignore all input until unmuted |
| "start listening" / "wake up" | Unmute — resume normal operation |

Priority order for stop commands: ringing alarm > playing music > pass to LLM.

## Configuration

Key settings in `glados_config.yml`:

```yaml
Glados:
  completion_url: "http://localhost:1234/v1"   # LLM server endpoint
  model: "qwen_qwen3.5-9b"                     # model name
  client_type: OPENAI                           # OPENAI, LANGCHAIN, or MISTRAL
  api_key: "lm-studio"
  voice_core: "glados"                          # "glados" (Piper/ONNX) or "kokoro" (Kokoro ONNX)
  voice_model: "glados.onnx"                    # model file or directory (in models/ dir)
  speaker_id: null                              # speaker ID (int for Piper, string for Kokoro)
  music_dir: ~/Music                            # music player directory
  speech_buffer_ms: 1200                        # ms of silence before finalizing speech
  max_context_messages: 20                      # max messages per activity context
  plugin_intent_threshold: 0.7                  # confidence threshold for intent classifier
  memory_enabled: true                          # persistent vector memory (ChromaDB)
  memory_db_path: "data/memory_db"              # memory database location
  memory_top_k: 5                               # past exchanges to retrieve per query
  openwakeword:
    threshold: 0.5
    models:
      - models/glados_wakeword.onnx
  mcp_servers:                                   # optional external MCP servers
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

### Voice Cores

GlaDOS supports switchable TTS backends:

| Voice Core | Engine | Default Model | Setup |
|------------|--------|---------------|-------|
| `glados` | Piper/ONNX | `glados.onnx` | Works out of the box |
| `kokoro` | Kokoro ONNX | `kokoro-82m-onnx/` | `pip install kokoro-onnx` + download models |

To use Kokoro:
1. `pip install kokoro-onnx`
2. Download model files (~340MB): `cd models/kokoro-82m-onnx && python get.py`
3. Set in config:
   ```yaml
   voice_core: "kokoro"
   voice_model: "kokoro-82m-onnx"
   speaker_id: "bf_isabella"   # see wiki for all voices
   ```

Voice names use `{accent}{gender}_{name}` — e.g. `bf_isabella` = British female Isabella, `am_adam` = American male Adam. Available: `af`, `af_bella`, `af_nicole`, `af_sarah`, `af_sky`, `am_adam`, `am_michael`, `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis`.

## Cuda Torch, you need to install the cuda version of torch, e.g:

   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
   pip install safetensors

## Plugins

Plugins use the MCP (Model Context Protocol) layer for tool registration. There are two patterns:

### Simple function plugin (`@mcp_tool` decorator)

```python
from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool

@mcp_tool(
    description="Get current weather for a location.",
    parameters={"location": {"type": "string", "description": "City name"}},
    required=["location"],
    intents=["what is the weather", "is it cold today"],
    process_output=True,
    activity=[Activity.GENERAL, Activity.UTILITIES],
    system_prompt="When reporting weather, include temperature and conditions.",
)
def handle_weather(location: str) -> str:
    return f"Sunny, 25C in {location}"
```

### Stateful plugin (`RunnableMCPPlugin` class)

For plugins that need background processes, event subscriptions, or lifecycle management:

```python
from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventHook, EventMessage

class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()

        # Plugin config is auto-loaded from glados_config.yml
        self.greeting = self.plugin_config.get("greeting", "hello")

        # Add guidance to the system prompt
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
        pass  # periodic background work
```

### Plugin Configuration

Plugins can load config from `glados_config.yml` under the `plugins` key. The `name` field is matched
against the **class name** (case-insensitive, underscores ignored):

```yaml
plugins:
  - name: music_player      # matches class MusicPlayer
    config:
      default_volume: 50
  - name: sarcasm_core       # matches class SarcasmCore
    config:
      enabled: true
  - name: three_laws         # matches class ThreeLaws
    config:
      enabled: true
```

- **RunnableMCPPlugin**: config is auto-loaded into `self.plugin_config` dict
- **Function plugins**: use `RunnableMCPPlugin.get_plugin_config("name")` (matches same way)

### System Prompt Additions

Plugins can append text to the system prompt to guide LLM behavior:

- **`@mcp_tool(system_prompt="...")`** — decorator param for function plugins
- **`self.register_system_prompt("...")`** — method on `RunnableMCPPlugin` for class plugins

These are appended as system messages to all activity contexts after the personality preprompt.

### Legacy decorator (still supported)

The old `@plugin_manager.register(FunctionRequest(...))` pattern still works and automatically
registers tools with the MCP server. See `glados/mcp/README.md` for full MCP integration details.

## MCP (Model Context Protocol)

GlaDOS uses MCP for standardized tool registration and execution. All plugin tools (both `@mcp_tool` and legacy)
are registered with an in-process `GladosMCPServer` that provides:

- MCP-standard tool schemas (`mcp.types.Tool`)
- Direct in-process tool execution (no transport overhead)
- OpenAI format conversion for passing to the LLM
- Activity-based tool filtering via `ToolMetadataRegistry`

External MCP servers can be connected via `glados_config.yml`:

```yaml
mcp_servers:
  - name: filesystem
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

See `glados/mcp/README.md` for detailed API documentation and examples.

## Event System

The EventSystem can be hooked into based on topics, e.g "role.name" = topic, eg:

| Topic | Description |
|-------|-------------|
| `system.tick` | 1Hz heartbeat |
| `system.wake_word_detected` | Wake word fired |
| `system.listen_for_response` | TTS finished, auto-listen enabled |
| `system.music_pause` / `system.music_resume` | Alarm pauses/resumes music |
| `tool.*` | Tool execution results |
| `display.*` | Content updates to the display screen |
| `status.*` | UI status toasts (listening, thinking, speaking, tool_call, idle, activity) |
| `vision.*` | Vision model requests and responses |
| `log.*` | Error and diagnostic logs |

### Subscribing to events
```python
event_system = EventSystem()
event_system.subscribe(
   "system.listen_for_response",
   EventHook(name="listen_for_response", callback=self.listen_for_response, priority=1)
)

def listen_for_response(self, event: EventMessage):
  pass
```

### Publishing events
```python
event_system.publish(
   EventMessage(
       role="tool",
       name="hello_world",
       content={
           "message": "Hello from a plugin! this is a self-test of the plug-in system."
       },
       process_output=True  # tells the LLM to parse this payload immediately
   )
)
```

# Installation Instruction
Try this simplified process, but be aware it's still in the experimental stage!  For all operating systems, you'll first
need to install Ollama to run the LLM.

## Models

The assistant uses the OpenAI python client, which I use with ollama hosted models locally, you can probably use online
OpenAI client compatible services, but I have not tested it.

### The Chat Model

The main chat model I use is a 8B chat model, e.g: `ollama pull llama3.1`

### The Vision Model

Testing the vision model can be done by running the model on a separate host, but be aware, this is just a POC that can
look at a directory of images and describe them, and feed that back to the chat model.

`ollama pull hf.co/second-state/Llava-v1.5-7B-GGUF:latest`

You need to set `OLLAMA_HOST` environment variable on the vision model host to the IP of the host, NOT `0.0.0.0`, e.g:
`OLLAMA_HOST=10.0.0.2`

## Install Drivers if necessary
If you are an Nvidia system with CUDA, make sure you install the necessary drivers and CUDA, info here:
https://onnxruntime.ai/docs/install/

If you are using another accelerator (ROCm, DirectML etc.), after following the instructions below for your platform,
follow up with installing the  [best onnxruntime version](https://onnxruntime.ai/docs/install/) for your system.

## Set up a local LLM server:
1. Download and install [Ollama](https://github.com/ollama/ollama) for your operating system.
2. Once installed, download a small 2B model for testing, at a terminal or command prompt use: `ollama pull llama3.2`
3. The vision model used on a separate host is `ollama pull hf.co/second-state/Llava-v1.5-7B-GGUF:latest`

Note: You can use any OpenAI or Ollama compatible server, local or cloud based. Just edit the glados_config.yaml and
update the completion_url, model and the api_key if necessary. LM Studio also works well with the OpenAI-compatible endpoint.

## Windows Installation Process
1. Open the Microsoft Store, search for `python` and install Python 3.12
2. Download this repository, either:
   1. Download and unzip this repository somewhere in your home folder, or
   2. If you have Git set up, `git clone` this repository using `git clone github.com/unixunion/glados.git`
3. In the repository folder, run the `install_windows.bat`, and wait until the installation in complete.
4. Double click `start_windows.bat` to start GLaDOS!

## macOS Installation Process
Untested

## Linux Installation Process
Untested

1. Install the PortAudio library, if you don't yet have it installed:

         sudo apt update
         sudo apt install libportaudio2

2. Download this repository, either:
   1. Download and unzip this repository somewhere in your home folder, or
   2. In a terminal, `git clone` this repository using `git clone github.com/dnhkng/glados.git`
3. In a terminal, go to the repository folder and run these commands:

         chmod +x install_ubuntu.sh
         chmod +x start_ubuntu.sh

4. In the a terminal in the GLaDOS folder, run `./install_ubuntu.sh`, and wait until the installation in complete.
5. Run  `./start_ubuntu.sh` to start GLaDOS!

## Changing the LLM Model

To use other models, use the command:
```ollama pull {modelname}```
and then add {modelname} to glados_config.yaml as the model. You can find [more models here!](https://ollama.com/library)

## Common Issues
New architecture, no idea what gremlins there are.

if you see lots of TTS like this instead of calling functions, it is related to too many plugins in the context or the system
preprompt is doing something funky with the json internals.
`Generating TTS for: {"type" "function","name" "get camera feed","parameters{"query" "","room" ""}}`
