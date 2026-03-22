# LLM Model Guide

GlaDOS uses tool/function calling to interact with plugins (15-30+ tools). This puts specific demands on the LLM.

## Requirements

- **Tool calling support** — the model must reliably generate structured function calls
- **Low latency** — voice assistant needs fast responses
- **Brief responses** — models that ramble waste TTS time
- **Context window** — each tool definition is ~100-200 tokens, so 20 tools eat ~2,000-4,000 tokens

## What to Look For

- Models fine-tuned for **function/tool calling** (not just chat)
- Good **instruction following** — respects system prompt constraints
- Adequate **context window** (8K minimum, 32K+ preferred)
- **Q4_K_M** quantization is the sweet spot for quality vs. speed

## Recommended Models by Hardware

| VRAM / Unified Memory | Models | Notes |
|----------------------|--------|-------|
| **8GB** | Qwen 2.5 3B, Llama 3.2 3B | Minimal tool calling |
| **16GB** | Qwen 2.5 7B, Qwen 3 8B | Workable with <15 tools |
| **32GB** | Qwen 2.5 32B (Q4), Mistral Small 24B | Good tool calling, 20+ tools |
| **64GB+** | Qwen 2.5 72B (Q4), Llama 3.1 70B | Excellent, 50+ tools |
| **NVIDIA 24GB** | Qwen 2.5 32B (Q4) | Single GPU |
| **NVIDIA 48GB** | Qwen 2.5 72B (Q4), Llama 3.1 70B | Best open-source |

## Known Issues

- **Thinking models** (Qwen 3.x) may leak chain-of-thought into responses. Use `<think>` tag stripping or switch to instruct models.
- **Small models (<7B)** often output tool calls as raw text instead of structured JSON.
- **Models not trained for function calling** will ignore tool definitions entirely.

## Servers

- **LM Studio** — `http://localhost:1234/v1`
- **Ollama** — `http://localhost:11434/v1`
- **Cloud APIs** — set `completion_url` and `api_key` in config
