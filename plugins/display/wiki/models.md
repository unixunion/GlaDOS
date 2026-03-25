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

## Benchmark Results

Tested with `tests/benchmark_models.py` — 58 single-turn tool selection tests + 11 multi-turn conversation chain steps (cooking flow, alarm flow, music flow, timer-while-cooking).

| Model | Accuracy | Avg TTFT | Avg Total | Notes |
|-------|----------|----------|-----------|-------|
| Qwen 2.5 32B Instruct | 94.2% | 2.5s | 3.8s | Best accuracy |
| Google Gemma 3 12B | 91.4% | 20s | 21s | Very accurate but very slow |
| Qwen 3 Coder 30B (MoE) | 89.7% | 0.5s | 0.8s | Best speed/accuracy tradeoff |
| Mistral Magistral Small | 89.9% | 1.4s | 1.9s | Strong all-rounder |
| Qwen 3 30B-A3B (MoE) | 86.2% | 0.5s | 0.8s | Fastest, only ~3B active params |
| OpenAI GPT-OSS 20B | 81.0% | 3.7s | 4.1s | Decent but slow |

### Running Benchmarks

```bash
# Test the currently loaded model
python tests/benchmark_models.py

# Test all installed models (auto load/unload via lms CLI)
python tests/benchmark_models.py --all

# Re-run specific models
python tests/benchmark_models.py --models qwen2.5-32b-instruct qwen/qwen3-coder-30b --retest

# View saved results without running tests
python tests/benchmark_models.py --report
```

Results are saved to `tests/benchmark_results/` as JSON. The `--all` flag auto-skips models already tested and only runs new test cases (delta mode).

## Known Issues

- **Thinking models** (Qwen 3.x) may leak chain-of-thought into responses. Use `<think>` tag stripping or switch to instruct models.
- **Small models (<7B)** often output tool calls as raw text instead of structured JSON.
- **Models not trained for function calling** will ignore tool definitions entirely.
- **select_recipe vs search_recipes** — some models confuse these in single-turn mode (no prior search context). Multi-turn chain tests catch this.

## Servers

- **LM Studio** — `http://localhost:1234/v1`
- **Ollama** — `http://localhost:11434/v1`
- **Cloud APIs** — set `completion_url` and `api_key` in config
