"""Extract core ingredient names from recipe dataset using LLM.

Processes all unique cleaned ingredients from the recipe cache,
extracts the core ingredient name via a fast LLM, and saves a
mapping file for use by the recipe search system.

Usage:
    python tools/extract_core_ingredients.py
    python tools/extract_core_ingredients.py --url http://localhost:1234/v1 --model liquid/lfm2.5-1.2b
    python tools/extract_core_ingredients.py --resume   # continue from last checkpoint
    python tools/extract_core_ingredients.py --stats     # just show stats from existing map

Output: plugin_data/recipes/ingredient_map.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO")

OUTPUT_FILE = "plugin_data/recipes/ingredient_map.json"
CHECKPOINT_INTERVAL = 100  # save progress every N items

SYSTEM_PROMPT = (
    "Extract the main ingredient from this recipe line. "
    "Reply with ONLY the core ingredient name, lowercase, no quantities, units, or preparation instructions.\n"
    "Examples:\n"
    '"tablespoons cold unsalted butter, cut into 1/2-inch cubes" → butter\n'
    '"skinless, boneless chicken breasts, about 1 pound" → chicken breast\n'
    '"garlic cloves, finely chopped" → garlic\n'
    '"freshly ground black pepper" → black pepper\n'
    '"extra-virgin olive oil" → olive oil\n'
    '"teaspoon salt" → salt\n'
    '"one 15 1/2 ounce can red kidney beans, rinsed and drained" → kidney beans\n'
    '"nonstick vegetable oil spray" → cooking spray\n'
)


def load_existing_map() -> dict:
    """Load existing ingredient map if present."""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            return json.load(f)
    return {}


def save_map(ingredient_map: dict):
    """Save ingredient map to JSON."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(ingredient_map, f, indent=2, ensure_ascii=False)


def get_unique_ingredients() -> list[str]:
    """Load all unique cleaned ingredients from the recipe cache."""
    from plugins.recipes.recipe_api import load_recipes, recipes
    load_recipes("data/recipes/dataset.csv")

    unique = set()
    for r in recipes:
        unique.update(r.get("cleaned_ingredients", []))
    return sorted(unique)


def extract_one(client, model: str, ingredient: str) -> str:
    """Extract core ingredient name via LLM."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ingredient},
        ],
        max_tokens=20,
        temperature=0,
    )
    return response.choices[0].message.content.strip().lower()


def run_extraction(url: str, model: str, resume: bool = False):
    from openai import OpenAI
    from tqdm import tqdm

    client = OpenAI(base_url=url, api_key="not-needed")

    # Load ingredients
    print("Loading recipe data...")
    ingredients = get_unique_ingredients()

    # Load existing map for resume
    ingredient_map = load_existing_map() if resume else {}
    already_done = set(ingredient_map.keys())
    remaining = [i for i in ingredients if i not in already_done]
    print(f"Total: {len(ingredients)} | Already done: {len(already_done)} | Remaining: {len(remaining)}")

    if not remaining:
        print("All ingredients already processed!")
        show_stats(ingredient_map)
        return

    errors = 0
    pbar = tqdm(remaining, desc="Extracting", unit="ing",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}")
    try:
        for idx, ingredient in enumerate(pbar):
            try:
                result = extract_one(client, model, ingredient)
                ingredient_map[ingredient] = result
                pbar.set_postfix_str(f"{ingredient[:30]} → {result[:20]}", refresh=False)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                errors += 1
                ingredient_map[ingredient] = ingredient  # fallback: keep original
                if errors <= 5:
                    tqdm.write(f"  Error on '{ingredient[:50]}': {e}")
                elif errors == 6:
                    tqdm.write("  (suppressing further errors)")

            if (idx + 1) % CHECKPOINT_INTERVAL == 0:
                save_map(ingredient_map)
    except KeyboardInterrupt:
        pbar.close()
        print(f"\nInterrupted. Saving checkpoint...")
        save_map(ingredient_map)
        print(f"Saved {len(ingredient_map)} mappings to {OUTPUT_FILE}. Use --resume to continue.")
        return

    pbar.close()
    save_map(ingredient_map)
    print(f"Done! Processed {len(remaining)} ingredients ({errors} errors)")
    show_stats(ingredient_map)


def show_stats(ingredient_map: dict = None):
    """Show statistics about the ingredient map."""
    if ingredient_map is None:
        ingredient_map = load_existing_map()

    if not ingredient_map:
        print("No ingredient map found.")
        return

    from collections import Counter

    core_values = list(ingredient_map.values())
    core_counter = Counter(core_values)
    unique_core = len(core_counter)

    print(f"\n{'='*50}")
    print(f"Ingredient Map Statistics")
    print(f"{'='*50}")
    print(f"Total mapped: {len(ingredient_map)}")
    print(f"Unique core ingredients: {unique_core}")
    print(f"Compression ratio: {len(ingredient_map)/unique_core:.1f}x")
    print(f"\nTop 30 core ingredients (by frequency):")
    for core, count in core_counter.most_common(30):
        print(f"  {count:5d}  {core}")

    # Show how many unique core ingredients there are
    print(f"\nDistribution:")
    print(f"  Core ingredients appearing 1 time: {sum(1 for c in core_counter.values() if c == 1)}")
    print(f"  Core ingredients appearing 2-5: {sum(1 for c in core_counter.values() if 2 <= c <= 5)}")
    print(f"  Core ingredients appearing 5+: {sum(1 for c in core_counter.values() if c > 5)}")
    print(f"  Core ingredients appearing 50+: {sum(1 for c in core_counter.values() if c >= 50)}")


def main():
    parser = argparse.ArgumentParser(description="Extract core ingredients from recipe dataset via LLM")
    parser.add_argument("--url", default="http://localhost:1234/v1", help="LLM API URL")
    parser.add_argument("--model", default=None, help="Model name (default: auto-detect)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--stats", action="store_true", help="Show stats from existing map")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    # Auto-detect model
    if not args.model:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=args.url, api_key="not-needed")
            models = client.models.list()
            args.model = models.data[0].id
            print(f"Using model: {args.model}")
        except Exception as e:
            print(f"Could not detect model: {e}")
            sys.exit(1)

    run_extraction(args.url, args.model, resume=args.resume)


if __name__ == "__main__":
    main()