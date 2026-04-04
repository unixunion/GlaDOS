# GlaDOS Wiki

A voice-first home assistant with pluggable architecture, LLM tool calling, and knowledge retrieval.

**WARNING!** GLaDOS is maniacal, and ultimately evil, so be careful connecting her to real world stuff.

## User Guide

### Getting Started
- [Installation](installation.md) — Setup for Windows, macOS, Linux, Raspberry Pi
- [Voice & Speech](voice.md) — Wake word, interrupting, mute/unmute, quick commands
- [LLM Models](models.md) — Recommended models by hardware tier, benchmarks

### Features
- [Functions Quick Reference](functions.md) — All voice commands at a glance
- [Shopping List](shopping.md) — Add items, categories, recurring, mobile PWA, planning mode
- [Pantry & Inventory](pantry.md) — Storage locations, expiry tracking, shelf life estimation
- [Recipes & Cooking](recipes.md) — Recipe search, cooking steps, ingredient search, recipe integration
- [Meal Planning](meal-planner.md) — Favorites, weekly meal plan, smart shopping list generation
- [Knowledge Base](knowledge.md) — Ask factual questions backed by Wikipedia and other sources

## Developer Guide

### Architecture & Concepts
- [Architecture](architecture.md) — System overview, activity contexts, event system, chat pipeline
- [NLP & Hybrid Mode](nlp-mode.md) — Hybrid NLP+LLM routing, pure NLP mode, coverage table
- [MCP Integration](mcp.md) — Model Context Protocol layer, tool registration

### Building Plugins
- [Writing Plugins](plugins.md) — `@mcp_tool`, `RunnableMCPPlugin`, NLP support, UI actions, chat hooks
- [Plugin Display Views](plugin-display.md) — Custom views and dashboard cards, JS module contract

### Operations
- [Configuration](configuration.md) — Full `glados_config.yml` settings reference
- [Log Analyzer](log-analyzer.md) — Ring buffer log capture, error analysis
- [Testing](testing.md) — Test suites, benchmarks, Makefile commands
