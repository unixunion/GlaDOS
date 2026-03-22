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

## Plugin Discovery

Plugins are auto-discovered from the `plugins/` directory on startup. Any module with a `RunnablePlugin` or `RunnableMCPPlugin` subclass is instantiated and started automatically.
