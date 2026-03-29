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

Tested with `tests/benchmark_models.py` — 58 single-turn tool selection tests + 16 reasoning tests + 11 multi-turn conversation chain steps (cooking flow, alarm flow, music flow, timer-while-cooking).

The benchmark includes three test types:
- **Tool selection**: direct "set a timer for 5 minutes" → correct tool call
- **Reasoning**: indirect "what should I wear today" → must infer weather check needed
- **Conversation chains**: multi-turn flows like search recipe → select → display

| Model | Accuracy | Avg TTFT | Avg Total | Notes |
|-------|----------|----------|-----------|-------|
| Qwen 2.5 7B Instruct Uncensored | 93.8% | 0.7s | 1.1s | Best overall — fast + accurate |
| Qwen 2.5 32B Instruct | 90.0% | 2.5s | 4.3s | High accuracy, slower |
| Qwen 2.5 7B Instruct 1M | 88.8% | 0.5s | 0.8s | Fastest, 1M context window |
| Mistral Magistral Small | 89.9% | 1.4s | 2.0s | Strong all-rounder |
| Qwen 3 30B-A3B (MoE) | 88.4% | 0.5s | 0.8s | Fast MoE, ~3B active params |
| Qwen 3 Coder 30B (MoE) | 86.2% | 0.6s | 0.9s | Good for code-heavy tasks |
| Qwen 2.5 14B Instruct MLX | 85.0% | 1.1s | 1.7s | Mid-range balance |
| Qwen 2.5 Coder 14B Instruct | 84.1% | 1.5s | 2.4s | Code-tuned |
| Google Gemma 3 12B | 91.4%* | 20s | 21s | Very accurate but extremely slow |
| OpenAI GPT-OSS 20B | 81.0%* | 3.7s | 4.1s | Decent but slow |

*Gemma and GPT-OSS tested with fewer test cases (58 vs 80). All others include reasoning tests.

### Running Benchmarks

```bash
# Test the currently loaded model
python tests/benchmark_models.py

# Test all installed models (auto load/unload via lms CLI)
python tests/benchmark_models.py --all

# Test models matching a pattern
python tests/benchmark_models.py --match qwen2.5

# Re-run specific models
python tests/benchmark_models.py --models qwen2.5-32b-instruct qwen/qwen3-coder-30b --retest

# View saved results without running tests
python tests/benchmark_models.py --report
```

Results include a **Reasoning Tests** breakdown showing how well each model handles indirect/inferential requests separately from keyword-based tool routing.

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

## Dual-Model Architecture

GlaDOS can use two models simultaneously — a **main model** for chat/tool calling and a **fast model** for lightweight tasks:

| Role | Config Key | Example Model | Used For |
|------|-----------|---------------|----------|
| **Main** | `model` | `qwen/qwen3-30b-a3b-2507` | Chat, tool calling, response generation |
| **Fast** | `knowledge_rewrite_model` | `liquid/lfm2.5-1.2b` | Knowledge query rewriting, pantry item classification |

The fast model handles simple classification/rewriting tasks (~50 tokens output) that don't need a large model. This keeps the main model free for conversation while background tasks run on the small model.

```yaml
# glados_config.yml
model: "qwen/qwen3-30b-a3b-2507"           # Main chat model
knowledge_query_mode: "rewrite"              # Use fast model for query rewriting
knowledge_rewrite_model: "liquid/lfm2.5-1.2b"  # 1.2B model for fast tasks
# knowledge_rewrite_url: null                # null = same LM Studio endpoint
```

### Tasks using the fast model

- **Knowledge query rewriting** — reformulates conversational follow-ups into focused search queries before hitting Qdrant
- **Pantry item classification** — classifies items as "ingredient" vs "ready_meal" when stored (background, fire-and-forget)
- **Batch reclassification** — "reclassify the pantry" processes all items through the fast model

### LM Studio Setup

Both models must be loaded simultaneously in LM Studio:

1. Go to **Developer** tab
2. Load your main model (e.g. `qwen/qwen3-30b-a3b-2507`)
3. Load the fast model (e.g. `liquid/lfm2.5-1.2b`)
4. **Important**: Go to `Settings → Developer → JIT models auto-evict = Off` so both stay in memory
5. LM Studio routes API requests by the `model` parameter — each call goes to the right model

### Recommended fast models

Benchmarked with `python tests/benchmark_knowledge.py`:

| Model | Knowledge Hit Rate | Avg Latency |
|-------|-------------------|-------------|
| `liquid/lfm2.5-1.2b` | 100% | 564ms |
| `qwen2.5-1.5b-instruct@8bit` | 100% | 798ms |
| `qwen/qwen3-4b-2507` | 100% | 827ms |
| `phi-3-mini-4k-instruct` | 90% | 970ms |

Thinking models (`qwen3-4b-thinking`, `nemotron-3-nano`) output `<think>` tags and fail — avoid them for rewrite tasks.
