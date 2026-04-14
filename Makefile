# GlaDOS test runner
# Usage:
#   make test          — fast unit tests (no external services)
#   make test-all      — unit + integration + browser tests
#   make test-ui       — Playwright browser tests only
#   make test-knowledge — knowledge RAG benchmark (requires Qdrant + LM Studio)
#   make test-models   — LLM tool-calling benchmark (requires LM Studio)

.PHONY: test test-all test-ui test-knowledge test-models tune-nlp lint

# ── Fast unit tests (default) ────────────────────────────────────────────
test:
	pytest tests/ -v --tb=short

# ── All tests including browser ──────────────────────────────────────────
test-all:
	pytest tests/ -v --tb=short -m "not benchmark"

# ── Browser UI tests (Playwright) ───────────────────────────────────────
test-ui:
	pytest tests/test_display_ui.py -v --tb=short -m browser

# ── Knowledge RAG benchmark ─────────────────────────────────────────────
# Requires: Qdrant running, wikipedia collection populated
# Optional: LM Studio running (for rewrite mode)
test-knowledge:
	python tests/benchmark_knowledge.py --modes raw,context

# Knowledge benchmark with LLM rewrite (requires LM Studio)
test-knowledge-rewrite:
	python tests/benchmark_knowledge.py --modes raw,context,rewrite

# Knowledge benchmark cycling all LM Studio models
test-knowledge-all-models:
	python tests/benchmark_knowledge.py --all-models

# ── LLM tool-calling benchmark ──────────────────────────────────────────
# Requires: LM Studio running with a model loaded
test-models:
	python tests/benchmark_models.py

# ── NLP threshold tuning ─────────────────────────────────────────────────
# Analyzes intent classification scores and recommends optimal thresholds
tune-nlp:
	python tools/tune_nlp_thresholds.py --report

# Apply recommended thresholds to glados_config.yml
tune-nlp-apply:
	python tools/tune_nlp_thresholds.py --apply

# ── Run the app ─────────────────────────────────────────────────────────
run:
	python main.py

run-text:
	python main.py --no-speech
