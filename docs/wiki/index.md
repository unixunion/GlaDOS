# GlaDOS Wiki

A voice-first home assistant with pluggable architecture, LLM tool calling, and knowledge retrieval.

**WARNING!** GLaDOS is maniacal, and ultimately evil, so be careful connecting her to real world stuff.

## User Guide

- [Installation](installation.md) — Setup for Windows, macOS, Linux
- [Configuration](configuration.md) — `glados_config.yml` settings reference
- [Voice Commands](voice.md) — Wake word, mute/unmute, intercepted commands
- [Functions](functions.md) — Timers, alarms, recipes, music, display, memory, vision, unit conversion, arithmetic
- [Shopping List & Pantry](pantry.md) — Shopping list, pantry inventory, expiry tracking, recipe integration
- [Knowledge Base](knowledge.md) — RAG with Qdrant, ZIM file ingestion, query modes (raw/context/rewrite)
- [LLM Models](models.md) — Recommended models by hardware tier, benchmarks
- [NLP Mode](nlp-mode.md) — Pure NLP mode for low-power devices, NLP coverage table

## Developer Guide

- [Architecture](architecture.md) — System overview, activity contexts, event system, pipeline
- [Plugins](plugins.md) — Writing plugins with `@mcp_tool`, `RunnableMCPPlugin`, and chat pipeline hooks
- [MCP Integration](mcp.md) — Model Context Protocol layer, tool registration, external servers
- [Log Analyzer](log-analyzer.md) — Ring buffer log capture, error analysis, saved reports
- [Testing](testing.md) — Test suites, benchmarks, Makefile commands
