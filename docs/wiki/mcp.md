# MCP Integration

GlaDOS uses MCP (Model Context Protocol) for standardized tool registration and execution.

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
  get_openai_tools() --> filtered tools sent to LLM
```

## Components

| Component | Purpose |
|-----------|---------|
| `GladosMCPServer` | In-process MCP server, stores tools as `mcp.types.Tool`, executes directly |
| `ToolMetadataRegistry` | GlaDOS metadata (intents, activity, process_output) per tool |
| `MCPPluginAdapter` | Bridges legacy `FunctionRequest` to MCP |
| `@mcp_tool` | Clean decorator for function plugins |
| `RunnableMCPPlugin` | Base class for stateful plugins with MCP tools |
| `MCPClientBridge` | Connects to external MCP servers via stdio |

## External MCP Servers

Connect to community or custom MCP servers via `glados_config.yml`:

```yaml
mcp_servers:
  - name: filesystem
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

External tools are namespaced as `{server_name}__{tool_name}` and registered with default metadata (`activity=[GENERAL]`, `process_output=True`).

## Activity Filtering with MCP

The LLM never sees all tools. The flow is:

1. IntentClassifier predicts which tool the user wants
2. Tool's activity is looked up in ToolMetadataRegistry
3. MessageManager switches to that activity context
4. Only tools matching the activity are sent to the LLM

See `glados/mcp/README.md` for full API documentation and code examples.
