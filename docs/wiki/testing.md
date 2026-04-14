# Testing

GlaDOS has several test suites covering NLP intent classification, pantry/shopping operations, ingredient parsing, and display UI end-to-end testing.

## Running Tests

```bash
make test                     # Fast unit tests (no external services needed)
make test-all                 # Unit + browser tests
make test-ui                  # Playwright browser tests only
make test-knowledge           # Knowledge RAG benchmark (requires Qdrant)
make test-knowledge-rewrite   # Knowledge benchmark + LLM rewrite mode (requires LM Studio)
make test-models              # LLM tool-calling benchmark (requires LM Studio)
```

Or directly with pytest:

```bash
pytest tests/ -v              # Unit tests only (default, skips browser/benchmark)
pytest tests/ -v -m browser   # Browser tests only
pytest tests/ -v -m "not benchmark"  # Everything except benchmarks
```

## Test Suites

### NLP Intent Classification (`tests/test_nlp.py`)

Tests intent classification accuracy, NLP parameter extraction, and end-to-end dispatch for all tools.

```bash
pytest tests/test_nlp.py -v
```

**What it covers:**
- Word-to-number conversion, duration parsing, keyword extraction
- Intent classification for 40+ phrases across all tools (timers, alarms, recipes, music, shopping, pantry, cooking context, memory)
- NLP handler registration verification (all expected handlers present)
- Parameter extraction for timers, alarms, weather, music, recipes, shopping, pantry
- End-to-end NLP dispatch (classify → extract → call tool → format response)
- Scoped classification (activity-filtered intent matching)
- Cooking session flow (recipe select → step navigation)
- Cross-context routing
- Polite/casual form resilience

**Scorecard:** Run with `-v` to see a full per-tool accuracy scorecard showing which phrases route correctly and at what confidence.

### Pantry & Shopping List (`tests/test_pantry.py`)

Tests all pantry plugin operations using a temporary data directory (never affects real data).

```bash
pytest tests/test_pantry.py -v
```

**What it covers:**
- Shopping list: add, remove, show, duplicate detection, recurring items, complete shopping
- Pantry: store item, find item, set expiry, check expiring, show pantry, manage locations
- Category auto-assignment (eggs → dairy, chicken → meat, etc.)
- NLP extraction for all pantry tools (add, remove, store, expiry, find, complete)
- Shopping sub-contexts: enter/exit planning mode, add items in planning, quantity updates with word numbers, multi-item "X and Y" parsing, voice text cleaning
- Date parsing: ISO format, "tomorrow", day-only ("the 24th")

**Data safety:** Uses `PANTRY_DATA_DIR` env var pointed at a temp directory. Real shopping list and pantry data are never touched.

### Ingredient Parser (`tests/test_ingredient_parser.py`)

Tests the ingredient parsing module that separates quantity, modifiers, and item names from recipe ingredient strings.

```bash
pytest tests/test_ingredient_parser.py -v
```

**What it covers:**
- Quantity extraction: tablespoons, cups, mixed fractions (1 1/2), count-only, no quantity
- Modifier extraction: fresh, finely chopped, multiple modifiers
- Parenthetical stripping: (optional), (for frying...), ", divided"
- Non-food filtering: thermometer, parchment paper vs real food
- List parsing with non-food filtering
- Edge cases: unicode fractions (½), leading dashes/bullets, empty strings

### Display UI End-to-End (`tests/test_display_ui.py`)

Browser-based tests using Playwright that verify the display UI works correctly.

```bash
# Headless (CI-friendly)
pytest tests/test_display_ui.py -v

# With visible browser (for debugging)
pytest tests/test_display_ui.py -v --headed
```

**Prerequisites:**
```bash
pip install playwright pytest-playwright
python -m playwright install chromium
```

**What it covers:**
- Dashboard: loads with 4 cards, clock visible, back button hidden
- Navigation: card click → full-screen view, back button → previous view, view history stack
- Chat drawer: toggle open/close, backdrop close, input/send/stop buttons
- Dashboard cards: quick-add shopping input, recipe search input
- Shopping list view: renders items, add form, checkbox toggle
- Timer overlay: hidden by default
- Mobile: bottom nav visible on phone viewport (375px), hidden on desktop (1024px)

**Note:** Spins up a test Flask/SocketIO server on port 5099 with temporary data. Does not interfere with the running GlaDOS instance.

### Knowledge RAG Benchmark (`tests/benchmark_knowledge.py`)

Measures retrieval quality across different query modes (raw, context, rewrite) and LLM models.

```bash
# Test raw + context modes (no LLM needed, just Qdrant)
python tests/benchmark_knowledge.py --modes raw,context

# Test all modes with specific rewrite models
python tests/benchmark_knowledge.py --models liquid/lfm2.5-1.2b qwen2.5-1.5b-instruct@8bit

# Test all loaded LM Studio models
python tests/benchmark_knowledge.py --all-models
```

**Requires:** Running Qdrant with populated wikipedia collection. For rewrite mode, a running LM Studio server.

10 test cases covering direct questions, conversational follow-ups, pronoun references, and topic continuations. Reports hit rate and latency per mode/model. Results saved to `tests/benchmark_results/`.

### Ingredient Extraction Benchmark (`tests/benchmark_ingredient_extraction.py`)

Tests LLM accuracy at extracting core ingredient names from recipe ingredient strings. 51 ground-truth cases covering simple items ("salt"), quantities ("tablespoons olive oil"), qualifiers ("skinless, boneless chicken breasts"), and edge cases ("nonstick vegetable oil spray").

```bash
# Auto-detect and test all loaded models
python tests/benchmark_ingredient_extraction.py

# Test specific models
python tests/benchmark_ingredient_extraction.py --models qwen/qwen3-4b-2507 liquid/lfm2.5-1.2b

# Custom LLM server URL
python tests/benchmark_ingredient_extraction.py --url http://localhost:1234/v1
```

**Requires:** Running LLM server (LM Studio, Ollama, etc.)

Reports accuracy, average latency, and specific failures per model. Used to select the best model for the ingredient map backfill (`tools/extract_core_ingredients.py`).

### Recipe Qdrant Ingestion (`tools/ingest_recipes_qdrant.py`)

Batch embeds all recipes into the `recipe_ingredients` Qdrant collection for semantic search.

```bash
# Build/rebuild the collection
python tools/ingest_recipes_qdrant.py

# Check collection status
python tools/ingest_recipes_qdrant.py --stats

# Custom Qdrant URL
python tools/ingest_recipes_qdrant.py --url http://localhost:6333
```

**Requires:** Running Qdrant server, sentence-transformers installed.

The collection auto-rebuilds at GlaDOS startup if `recipe_qdrant_enabled: true`, but the offline tool is faster for initial setup (~2 min for 13.5K recipes).

### Recipe Categorization (`tools/categorize_recipes.py`)

Classifies all recipes by cuisine and type using a local LLM. Each recipe's title + top ingredients are sent to the model for classification into 22 categories (including Mediterranean, Asian, Indian, Mexican & Latin, Middle Eastern, American Comfort).

```bash
# Full run
python tools/categorize_recipes.py --model qwen/qwen3-4b-2507

# Resume from checkpoint
python tools/categorize_recipes.py --resume

# View category distribution
python tools/categorize_recipes.py --stats
```

**Requires:** Running LLM server (LM Studio, Ollama, etc.)

Output: `plugin_data/recipes/recipe_categories.json`. Applied automatically at startup — overrides the built-in keyword-based categories. The browse view shows all LLM-inferred categories.

### Meal Planner (`tests/test_meal_planner.py`)

End-to-end tests for the meal planning workflow: favorites, weekly planning, shopping list generation, and the optimizer.

```bash
# Run separately (integration marker — uses plugin singletons)
pytest tests/test_meal_planner.py -m integration -v
```

**What it covers:**
- Favorites: save, duplicate rejection, remove, show
- Meal planning: plan for specific day, auto-assign day, multiple meals per day, remove by day/name
- Household setup and persistence
- Suggest meals: returns results using pantry + favorites
- Generate shopping list: adds missing ingredients, skips pantry items, suggests staples
- Full workflow: save favorites → set household → plan 3 meals → generate list → verify counts

**Note:** Marked as `integration` because it registers plugins with singletons that would pollute other test modules. Not included in `make test` — run separately.

### Meal Planner LLM Conversation (`tests/test_meal_planner_llm.py`)

End-to-end test with a real LLM. Sends natural language to the ChatClient, verifies the LLM correctly orchestrates meal planning tools in multi-turn conversation.

```bash
pytest tests/test_meal_planner_llm.py -m benchmark -v
```

**Requires:** Running LLM server (LM Studio). Takes ~30s.

**What it covers:**
- "suggest healthy meals for the week" → LLM calls `suggest_weekly_meals` with preferences
- Multi-turn: suggest → "plan those for the week" → LLM calls `plan_meal` for each day

This tests the LLM-driven orchestration flow where the model chains tools based on conversation context — not just single-tool routing.

### NLP Threshold Tuner (`tools/tune_nlp_thresholds.py`)

Auto-tunes per-tool NLP confidence thresholds by running all intent test cases and analyzing score distributions.

```bash
make tune-nlp            # summary report with per-tool scores + recommendations
make tune-nlp-apply      # write recommended thresholds to glados_config.yml

# Or directly:
python tools/tune_nlp_thresholds.py              # summary
python tools/tune_nlp_thresholds.py --report      # detailed per-tool breakdown with individual phrases
python tools/tune_nlp_thresholds.py --apply       # apply to config
```

**What it does:**
1. Loads all plugins and the 189+ intent test cases
2. Classifies every phrase, records confidence scores per tool
3. Computes optimal thresholds: midpoint between lowest correct score and highest false positive
4. Outputs a report showing per-tool score ranges, current vs recommended thresholds
5. With `--apply`, writes per-tool thresholds to `glados_config.yml` and updates global hybrid threshold

**When to run:** After adding or modifying intent training examples, adding new tools, or when intent dilution causes misroutes.

### Model Benchmarks (`tests/benchmark_models.py`)

Benchmarking tool for comparing LLM models on tool-calling tasks.

```bash
python tests/benchmark_models.py
```

## Writing Tests

### For new tools/plugins

1. Add intent classification test cases to `INTENT_TEST_CASES` in `tests/test_nlp.py`
2. Add NLP extraction tests to the appropriate `Test*Extraction` class
3. Add the handler name to `EXPECTED_HANDLERS` in `TestNLPHandlerRegistration`
4. For pantry/shopping operations, add tests to `tests/test_pantry.py` (uses temp data dir)

### For display UI changes

Add Playwright tests to `tests/test_display_ui.py`. The `display_server` fixture provides a running server with test data.

```python
def test_my_feature(self, page, display_server):
    page.goto(display_server["url"])
    page.wait_for_selector(".dashboard")
    # Navigate, click, assert...
    page.locator("#my-button").click()
    page.wait_for_timeout(1500)  # SocketIO round-trip
    assert page.locator("#my-element").is_visible()
```

### Tips

- NLP tests load all plugins via `load_plugins("plugins")` — intent classification results depend on the full set of registered intents
- Pantry tests use `PANTRY_DATA_DIR` env var — always use a temp directory
- UI tests use port 5099 — don't conflict with the main display on 5001
- The event system ticker runs every 1s — SocketIO-dependent assertions may need `wait_for_timeout(1500-3000)`