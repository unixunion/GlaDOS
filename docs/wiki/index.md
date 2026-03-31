# GlaDOS Wiki

A voice-first home assistant with pluggable architecture, LLM tool calling, and knowledge retrieval.

**WARNING!** GLaDOS is maniacal, and ultimately evil, so be careful connecting her to real world stuff.

## User Guide

Start here to learn what GlaDOS can do and how to interact with her.

- [Voice & Speech](voice.md) — Wake word, interrupting, mute/unmute, quick commands
- [Functions](functions.md) — Everything you can ask: timers, alarms, recipes, music, display, memory, vision
- [Shopping List & Pantry](pantry.md) — Shopping list, pantry inventory, expiry tracking, put-away flow, recipe integration
- [Knowledge Base](knowledge.md) — Ask factual questions backed by Wikipedia and other sources
- [Installation](installation.md) — Setup for Windows, macOS, Linux, Raspberry Pi
- [LLM Models](models.md) — Recommended models by hardware tier, benchmarks

## Developer Guide

For extending GlaDOS with new plugins, understanding the architecture, or contributing.

- [Architecture](architecture.md) — System overview, activity contexts, event system, chat pipeline
- [Configuration](configuration.md) — Full `glados_config.yml` settings reference
- [Writing Plugins](plugins.md) — `@mcp_tool`, `RunnableMCPPlugin`, NLP support, UI actions, chat hooks
- [Plugin Display Views](plugin-display.md) — Custom views and dashboard cards, JS module contract
- [MCP Integration](mcp.md) — Model Context Protocol layer, tool registration
- [NLP & Hybrid Mode](nlp-mode.md) — Hybrid NLP+LLM routing, pure NLP mode, coverage table
- [Log Analyzer](log-analyzer.md) — Ring buffer log capture, error analysis
- [Testing](testing.md) — Test suites, benchmarks, Makefile commands
