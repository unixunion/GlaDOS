# Testing

GlaDOS has several test suites covering NLP intent classification, pantry/shopping operations, ingredient parsing, and display UI end-to-end testing.

## Running All Tests

```bash
# Run everything
pytest tests/ -v

# Run everything except UI tests (faster, no browser needed)
pytest tests/ -v --ignore=tests/test_display_ui.py
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

### Model Benchmarks (`tests/benchmark_models.py`)

Not a test suite — a benchmarking tool for comparing LLM models on tool-calling tasks.

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