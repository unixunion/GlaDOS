# GlaDOS Wiki

A maniacal home assistant with pluggable architecture and function calling support.

**WARNING!** GLaDOS is maniacal, and ultimately evil, so be careful connecting her to real world stuff.

## Pages

- [Architecture](architecture.md) — System overview, activity contexts, event system
- [Plugins](plugins.md) — Writing plugins with `@mcp_tool`, `RunnableMCPPlugin`, and chat pipeline hooks
- [MCP Integration](mcp.md) — Model Context Protocol layer, tool registration, external servers
- [LLM Models](models.md) — Recommended models by hardware tier, what to look for
- [Configuration](configuration.md) — `glados_config.yml` settings reference
- [NLP & Hybrid Mode](nlp-mode.md) — Hybrid NLP+LLM routing, pure NLP mode, plugin NLP handlers, testing
- [Voice Commands](voice.md) — Wake word, mute/unmute, intercepted commands
- [Functions](functions.md) — Timers, alarms, recipes, music, display, memory, vision, unit conversion, arithmetic
- [Shopping List & Pantry](pantry.md) — Shopping list, pantry inventory, expiry tracking, recipe integration
- [Knowledge Base](knowledge.md) — RAG with Qdrant, ZIM file ingestion, query modes (raw/context/rewrite), conversation RAG
- [Log Analyzer](log-analyzer.md) — Ring buffer log capture, error analysis, saved reports for debugging
- [Testing](testing.md) — Test suites: NLP, pantry, ingredient parser, Playwright UI, knowledge RAG benchmarks
- [Installation](installation.md) — Setup for Windows, macOS, Linux
