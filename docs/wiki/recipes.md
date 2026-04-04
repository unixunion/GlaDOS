# Recipes & Cooking

Recipe dataset with ~13,500 recipes and images across 15 categories. Browse, search, discover, and cook — with pantry integration and meal planning.

**Related pages**: [Shopping List](shopping.md) | [Pantry & Inventory](pantry.md) | [Meal Planning](meal-planner.md)

## Browsing Recipes

The recipe dashboard card has a **Browse** button that opens a category grid:

- **22 categories** (with LLM categorization): Breakfast, Soups & Stews, Salads, Pasta & Noodles, Chicken, Beef, Pork, Seafood, Vegetarian, Mediterranean, Asian, Indian, Mexican & Latin, Middle Eastern, American Comfort, Baking & Desserts, Bread, Appetizers & Snacks, Sides, Sauces & Condiments, Drinks & Cocktails, Other
- Tap a category to see all recipes in that category with thumbnails and pantry match %
- Heart button on every recipe card to save as a [favorite](meal-planner.md#favorites)
- Pagination for large categories ("Load More")

### Category Sources

Categories come from two sources, with the LLM version taking priority:

1. **Keyword inference** (built-in fallback) — matches recipe titles against keyword lists at startup. Fast, no setup needed, covers 15 basic categories.
2. **LLM categorization** (recommended) — a local LLM classifies each recipe by title + ingredients into 22 categories including cuisine types. Run once:

```bash
python tools/categorize_recipes.py --model qwen/qwen3-4b-2507
python tools/categorize_recipes.py --resume   # safe to interrupt and restart
python tools/categorize_recipes.py --stats     # view distribution
```

Categories are written directly into `recipes.json`. A background delta worker also auto-categorizes new recipes on startup (30s delay to avoid competing with the greeting).

### Recommended Models for Categorization

Benchmarked on 46 ground-truth recipes across meal type + cuisine:

| Model | Both Correct | Cuisine % | Avg Latency |
|-------|-------------|-----------|-------------|
| google/gemma-3n-e4b | 78.3% | **91.3%** | 863ms |
| qwen2.5-7b-instruct@8bit | **82.6%** | 89.1% | 614ms |
| google/gemma-3-12b | 80.4% | 91.3% | 1140ms |
| mistralai/mistral-small-3.2 | 80.4% | 91.3% | 1679ms |
| qwen/qwen3-4b-2507 | 73.9% | 87.0% | 440ms |

Configure in `glados_config.yml`:
```yaml
recipe_classify_model: "google/gemma-3n-e4b"
```

This model is also used for the delta ingredient extraction worker. Falls back to the main model if not set.

### Surprise Me

Two ways to discover recipes:

| Say this | What happens |
|----------|-------------|
| "surprise me" | Picks a random recipe weighted by pantry match — practical |
| "what should I cook" | Same — favors recipes you can actually make |
| "random recipe" | Picks from the full dataset — pure exploration |
| "surprise me with anything" | Same — ignores pantry |
| "random pasta recipe" | Category-filtered random |
| "surprise me with a dessert" | Category-filtered, pantry-aware |

Both variants are also available as buttons in the browse view and on the dashboard card.

## Searching Recipes

| Say this | What happens |
|----------|-------------|
| "find me a recipe for bread" | Search by keyword, returns a list |
| "search recipes for pizza" | Alternate search phrasing |
| "look up a recipe for cookies" | Another variant |
| "I need a recipe" | Vague search (may ask for specifics) |

- Results display on screen with thumbnails, ingredient counts, and pantry match %
- Say "the first one" or "select the lasagna" to choose a recipe

## Searching by Ingredients

| Say this | What happens |
|----------|-------------|
| "what can I make with chicken and rice" | Finds recipes using those ingredients |
| "recipes with garlic and mushrooms" | Ingredient-based search |
| "what can I cook with potatoes" | Single ingredient search |
| "find me something with beef and peppers" | Natural phrasing |

- Uses ingredient normalization — "chicken" matches "chicken breast", "boneless chicken thighs", etc.
- Optionally uses Qdrant semantic search for better matching (see below)

## Selecting & Cooking

| Say this | What happens |
|----------|-------------|
| "lets make apple pie" | Select and activate a recipe |
| "select the pizza recipe" | Select from previous search results |
| "the first one" / "number 3" | Positional selection from results |
| "lets cook spaghetti" | Natural cooking intent |

Selected recipes are automatically displayed on screen with image, ingredients, and steps.

### Cooking Steps

These commands work after a recipe has been selected:

| Say this | What happens |
|----------|-------------|
| "list the ingredients" / "what do I need" | Read all ingredients |
| "what are the steps" / "read the instructions" | Read all directions |
| "next step" / "what do I do next" | Advance to next step |
| "previous step" / "go back" | Go back one step |
| "repeat that" / "say that again" | Repeat current step |
| "first step" / "start from the beginning" | Jump to step 1 |
| "what are we making" | Current recipe name |

## Pantry Integration

| Say this | What happens |
|----------|-------------|
| "what can I make with what's in the pantry" | Ready meals + recipe suggestions from pantry contents |
| "what can I make before things expire" | Prioritizes expiring ingredients |
| "suggest a meal" / "what's for dinner" | General meal suggestion from pantry |
| "do we have the ingredients" | Checks pantry against selected recipe |
| "what ingredients are we missing" | Lists what to buy |
| "add the ingredients to the shopping list" | Adds missing recipe ingredients to list |

- "What's for dinner" uses the full pantry contents to find matching recipes
- "What can I make with chicken" uses specific ingredients you mention
- Ready meals (lasagna, frozen pizza) show separately from recipe suggestions
- Expiring items are prioritized in recipe suggestions
- All matching uses **normalized ingredient names** — "butter" in your pantry matches "cold unsalted butter" in a recipe

### Recipe Action Buttons

When viewing a recipe, three buttons appear:

- **Check Pantry** — shows which ingredients you have vs. missing
- **Add Missing to List** — adds only the missing ingredients to shopping list
- **+ Plan** — add recipe to your [weekly meal plan](meal-planner.md)
- **Heart** — save/unsave as a [favorite](meal-planner.md#favorites)

## Ingredient Normalization

Bridges the gap between how you say ingredient names ("chicken", "butter") and how recipes store them ("boneless, skinless chicken thighs", "cold unsalted butter, cut into cubes").

### How It Works

1. **Offline backfill**: A local LLM processes all unique recipe ingredients to extract canonical core names. This produces a mapping file (`plugin_data/recipes/ingredient_map.json`) with ~thousands of entries like `"boneless, skinless chicken thighs" → "chicken thigh"`.

2. **At startup**: The recipe cache is built with `core_ingredients` per recipe. The search index is built on these shorter, precise core names.

3. **When you add items**: Shopping list items are matched against known core ingredient names. High-confidence matches (>= 85%) are auto-normalized.

4. **Recipe matching**: Your pantry item "chicken" matches recipes containing "chicken breast" because both sides now use normalized names.

### Generating the Ingredient Map

Requires a local LLM server (LM Studio, Ollama, etc.):

```bash
python tools/extract_core_ingredients.py --model qwen/qwen3-4b-2507
python tools/extract_core_ingredients.py --resume   # continue from checkpoint
python tools/extract_core_ingredients.py --stats     # view existing map stats
```

### Semantic Search (Qdrant)

Optionally, recipe search can use vector similarity via Qdrant instead of keyword matching. This handles synonyms and related ingredients better.

Enable in `glados_config.yml`:
```yaml
recipe_qdrant_enabled: true
```

Requires Qdrant running (same instance as the knowledge base). Build the collection:
```bash
python tools/ingest_recipes_qdrant.py
```

The recipe search UI shows which method was used — "semantic search" or "basic matching".

When Qdrant is unavailable, recipe search falls back to fuzzy matching automatically.

### Disabling Normalization

```yaml
normalize_shopping_items: false
```

When disabled, items are stored exactly as you say them. Autocomplete suggestions still work.
