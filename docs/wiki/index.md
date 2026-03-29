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

## Developer Guide

- [Architecture](architecture.md) — System overview, activity contexts, event system, pipeline
- [Writing Plugins](plugins.md) — `@mcp_tool`, `RunnableMCPPlugin`, NLP support, UI actions, chat hooks
- [Plugin Display Views](plugin-display.md) — Custom views and dashboard cards, JS module contract, CSS classes
- [MCP Integration](mcp.md) — Model Context Protocol layer, tool registration, external servers
- [NLP & Hybrid Mode](nlp-mode.md) — Hybrid NLP+LLM routing, pure NLP mode, coverage table
- [Log Analyzer](log-analyzer.md) — Ring buffer log capture, error analysis, saved reports
- [Testing](testing.md) — Test suites, benchmarks, Makefile commands
