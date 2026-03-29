# GlaDOS Wiki

A maniacal home assistant with pluggable architecture and function calling support.

**WARNING!** GLaDOS is maniacal, and ultimately evil, so be careful connecting her to real world stuff.

## Pages

- [Architecture](architecture) — System overview, activity contexts, event system
- [Plugins](plugins) — Writing plugins with `@mcp_tool`, `RunnableMCPPlugin`, and chat pipeline hooks
- [MCP Integration](mcp) — Model Context Protocol layer, tool registration, external servers
- [LLM Models](models) — Recommended models by hardware tier, what to look for
- [Configuration](configuration) — `glados_config.yml` settings reference
- [NLP & Hybrid Mode](nlp-mode) — Hybrid NLP+LLM routing, pure NLP mode, plugin NLP handlers, testing
- [Voice Commands](voice) — Wake word, mute/unmute, intercepted commands
- [Functions](functions) — Timers, alarms, recipes, music, display, memory, vision, unit conversion, arithmetic
- [Shopping List & Pantry](pantry) — Shopping list, pantry inventory, expiry tracking, recipe integration
- [Knowledge Base](knowledge) — RAG with Qdrant, ZIM file ingestion, query modes (raw/context/rewrite), conversation RAG
- [Log Analyzer](log-analyzer) — Ring buffer log capture, error analysis, saved reports for debugging
- [Testing](testing) — Test suites: NLP, pantry, ingredient parser, Playwright UI, knowledge RAG benchmarks
- [Installation](installation) — Setup for Windows, macOS, Linux
