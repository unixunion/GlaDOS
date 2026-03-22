# GlaDOS MCP Integration

MCP (Model Context Protocol) layer for GlaDOS tool registration and execution.

## Architecture

```
Plugin registers tool (via @mcp_tool, RunnableMCPPlugin, or legacy @register)
        |
        v
  GladosMCPServer  +  ToolMetadataRegistry
        |                    |
        v                    v
  MCP Tool schemas     GlaDOS-specific metadata
  (name, desc,         (intents, activity,
   inputSchema)         process_output)
        |
        v
  get_openai_tools() ──> filtered tools sent to LLM
```

External MCP servers connect via `MCPClientBridge` and their tools are registered
into the same pipeline with default metadata.

## Components

| Component | File | Purpose |
|-----------|------|---------|
| `GladosMCPServer` | `server.py` | In-process MCP server. Stores tools as `mcp.types.Tool`, executes handlers directly. |
| `ToolMetadataRegistry` | `metadata.py` | Stores GlaDOS metadata (intents, activity, process_output) per tool. |
| `MCPPluginAdapter` | `adapter.py` | Bridges legacy `FunctionRequest` registration to MCP. |
| `@mcp_tool` | `decorators.py` | Clean decorator for simple function plugins. |
| `RunnableMCPPlugin` | `runnable_mcp_plugin.py` | Base class for stateful plugins with lifecycle + events + MCP tools. |
| `MCPClientBridge` | `client_bridge.py` | Connects to external MCP servers via stdio, registers their tools. |

## Simple Function Plugin (`@mcp_tool`)

The cleanest way to register a tool:

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

The decorator registers the tool with:
- `GladosMCPServer` (MCP schema + handler)
- `ToolMetadataRegistry` (intents, activity, process_output)
- `IntentClassifier` (if intents provided)
- `PluginSystem` (backward compat + system prompt)

## Stateful Plugin (`RunnableMCPPlugin`)

For plugins that need background processes, event subscriptions, or lifecycle management:

```python
from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventHook, EventMessage

class MyPlugin(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()

        # Plugin config auto-loaded from glados_config.yml (see Plugin Configuration below)
        self.max_items = self.plugin_config.get("max_items", 10)

        # Add LLM guidance for this plugin's tools
        self.register_system_prompt("When doing things, confirm the result to the user.")

        self.register_tool(
            handler=self.do_thing,
            description="Does a useful thing",
            parameters={
                "query": {"type": "string", "description": "What to do"},
            },
            required=["query"],
            intents=["do the thing", "run my plugin"],
            process_output=True,
            activity=[Activity.GENERAL],
        )

    def start(self):
        self.event_system.subscribe(
            "system.tick",
            EventHook("my_tick", callback=self._on_tick, priority=1),
        )

    def stop(self):
        self.event_system.unsubscribe("system.tick", "my_tick")

    def do_thing(self, query: str) -> dict:
        return {"status": "success", "result": f"Did: {query}"}

    def _on_tick(self, event: EventMessage):
        pass  # periodic background work
```

`RunnableMCPPlugin` provides:
- `self.register_tool(...)` — registers with MCP + metadata + intent + legacy systems
- `self.register_system_prompt("...")` — append text to the LLM system prompt
- `self.plugin_config` — dict of config loaded from `glados_config.yml`
- `self.event_system` — the GlaDOS EventSystem singleton for pub/sub
- `start()` / `stop()` — lifecycle hooks called automatically by the plugin loader

## Plugin Configuration

Plugins can load config from the `plugins` section of `glados_config.yml`.

The `name` field is matched against the **class name** (case-insensitive, underscores ignored).
For example, `music_player` matches class `MusicPlayer`, `sarcasm_core` matches `SarcasmCore`.

```yaml
plugins:
  - name: music_player       # matches class MusicPlayer
    config:
      default_volume: 50
  - name: sarcasm_core        # matches class SarcasmCore
    config:
      enabled: true
```

### RunnableMCPPlugin (automatic)

Config is auto-loaded in `__init__()` and available as `self.plugin_config`:

```python
class MusicPlayer(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        # self.plugin_config is already populated from YAML
        self.volume = self.plugin_config.get("default_volume", 30)
```

### Function plugins (static method)

```python
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin

config = RunnableMCPPlugin.get_plugin_config("my_plugin")
max_results = config.get("max_results", 5)
```

## System Prompt Additions

Plugins can append text to the system prompt to guide LLM behavior with their tools.
These are injected as system messages into all activity contexts after the personality preprompt.

### Via `@mcp_tool` decorator

```python
@mcp_tool(
    description="Search recipes",
    system_prompt="When selecting a recipe, interpret the user's selection based on prior results.",
    # ...other params
)
def search_recipes(query: str) -> dict:
    pass
```

### Via `RunnableMCPPlugin`

```python
class AlarmClock(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        self.register_system_prompt(
            "When an alarm fires, tell the user the alarm description. "
            "They can dismiss it by saying stop, cancel, or silence."
        )
```

### Via legacy PluginSystem

```python
plugin_manager = PluginSystem()
plugin_manager.register_system_prompt("Custom guidance for the LLM.")
```

## Using the MCP Server Directly

```python
from glados.mcp.server import GladosMCPServer

server = GladosMCPServer()

# Register a tool manually
server.register_tool(
    name="greet",
    description="Greet someone",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    handler=lambda name: f"Hello, {name}!",
)

# List tools (returns mcp.types.Tool objects)
tools = server.list_tools()

# Call a tool (returns mcp.types.CallToolResult)
result = server.call_tool("greet", {"name": "world"})
print(result.content[0].text)  # "Hello, world!"
print(result.isError)          # False

# Get tools in OpenAI format for the LLM
openai_tools = server.get_openai_tools()
# [{"type": "function", "function": {"name": "greet", ...}}]

# Filter by tool names (used by the activity system)
filtered = server.get_openai_tools(tool_names=["greet"])
```

## Activity Filtering

The LLM never sees all tools. The flow is:

```
1. User says "set a timer for 5 minutes"
2. IntentClassifier predicts: tool="set_timer", confidence=0.8
3. Lookup set_timer's activity: [UTILITIES, COOKING]
4. MessageManager switches to UTILITIES context
5. ToolMetadataRegistry.get_tools_for_activity(UTILITIES) -> filtered tool names
6. GladosMCPServer.get_openai_tools(tool_names=filtered) -> OpenAI schemas
7. Only UTILITIES tools are sent to the LLM
```

```python
from glados.mcp.metadata import ToolMetadataRegistry
from glados.mcp.server import GladosMCPServer
from glados.context.activity import Activity

registry = ToolMetadataRegistry()
server = GladosMCPServer()

# Get tool names for an activity
cooking_tools = registry.get_tools_for_activity(Activity.COOKING)
# ['set_timer', 'list_timers', 'get_current_time', 'search_recipes', ...]

# Get those tools in OpenAI format
openai_tools = server.get_openai_tools(tool_names=cooking_tools)

# Check process_output for a tool
should_process = registry.get_process_output("set_timer")  # True
```

## External MCP Servers

Connect to community or custom MCP servers. Their tools are registered into GlaDOS
with default metadata (`activity=[GENERAL]`, `process_output=True`, no intents).

### Config-based (glados_config.yml)

```yaml
mcp_servers:
  - name: filesystem
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
  - name: my_custom_server
    command: python
    args: ["-m", "my_server"]
```

### Programmatic

```python
from glados.mcp.client_bridge import MCPClientBridge
from glados.context.activity import Activity

bridge = MCPClientBridge()
bridge.connect_server(
    name="filesystem",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/home/user"],
    activity=[Activity.SYSTEM],  # override default activity
)
```

External tools are namespaced as `{server_name}__{tool_name}` to avoid collisions
with local plugins.

## Tool Execution

The `ToolExecutor` supports both legacy and MCP execution paths:

```python
from glados.llm.tool_executor import ToolExecutor
from glados.system.plugin import PluginSystem

executor = ToolExecutor(plugin_manager=PluginSystem())

# MCP path
result = executor.execute_tool_via_mcp("handle_weather", {"location": "Tokyo"})
# {"status": "success", "results": [{"tool": "handle_weather", "result": "Sunny, 25C in Tokyo"}]}

# Legacy path (used by ChatClient for OpenAI/LangChain tool_call objects)
# executor.execute_tool(tool_call, architecture=ClientType.OPENAI)
```

## Legacy Decorator (still supported)

The old `@plugin_manager.register(FunctionRequest(...))` pattern still works.
The `MCPPluginAdapter` automatically registers these tools with the MCP server.
New plugins should prefer `@mcp_tool` or `RunnableMCPPlugin.register_tool()`.
