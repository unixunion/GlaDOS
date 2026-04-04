"""Categorize recipes by meal type and cuisine using an LLM.

Reads recipes.json directly, classifies uncategorized recipes via LLM,
and writes categories back into recipes.json as it goes.

Usage:
    python tools/categorize_recipes.py
    python tools/categorize_recipes.py --model qwen/qwen3-4b-2507
    python tools/categorize_recipes.py --force    # re-categorize everything
    python tools/categorize_recipes.py --stats     # show category distribution

Operates on: plugin_data/recipes/recipes.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO")

RECIPES_FILE = "plugin_data/recipes/recipes.json"
CHECKPOINT_INTERVAL = 100

MEAL_TYPES = [
    "breakfast",
    "appetizers & snacks",
    "mains",
    "sides",
    "soups & stews",
    "salads",
    "baking & desserts",
    "bread",
    "drinks",
    "sauces & condiments",
]

CUISINES = [
    "american",
    "italian",
    "mexican & latin",
    "asian",
    "indian",
    "mediterranean",
    "middle eastern",
    "french",
    "british",
    "other",
]

SYSTEM_PROMPT = (
    "Classify this recipe on TWO axes. Reply with EXACTLY two lines:\n"
    "meal: <meal type>\n"
    "cuisine: <cuisine>\n\n"
    "MEAL TYPES:\n"
    + "\n".join(f"- {m}" for m in MEAL_TYPES)
    + "\n\nCUISINES:\n"
    + "\n".join(f"- {c}" for c in CUISINES)
    + "\n\n"
    "Guidelines:\n"
    "- 'mains' = primary dishes (chicken dinners, steak, stir-fry, casseroles, curries, tacos, etc.)\n"
    "- 'baking & desserts' = cakes, cookies, pies, candy, sweet pastries, chocolate\n"
    "- 'asian' = Chinese, Japanese, Korean, Thai, Vietnamese, etc.\n"
    "- 'mediterranean' = Greek, Turkish, Spanish, North African\n"
    "- 'american' = BBQ, burgers, mac & cheese, Cajun, Southern, comfort food\n"
    "- 'italian' = pasta, pizza, risotto, Italian-style dishes\n"
    "- 'other' cuisine = when no clear cuisine origin, generic/fusion, or unclear\n"
    "- Use your best judgment — the title and ingredients are strong signals\n\n"
    "Examples:\n"
    '"Chicken Tikka Masala | chicken, yogurt, garam masala" → meal: mains\\ncuisine: indian\n'
    '"Pad Thai | rice noodles, shrimp, peanuts" → meal: mains\\ncuisine: asian\n'
    '"Classic Lasagna | pasta, beef, ricotta" → meal: mains\\ncuisine: italian\n'
    '"Chocolate Chip Cookies | butter, sugar, flour" → meal: baking & desserts\\ncuisine: american\n'
    '"French Onion Soup | onion, gruyère, beef broth" → meal: soups & stews\\ncuisine: french\n'
    '"Guacamole | avocado, lime, cilantro" → meal: appetizers & snacks\\ncuisine: mexican & latin\n'
    '"Overnight Oats | oats, milk, honey" → meal: breakfast\\ncuisine: other\n'
    '"Greek Salad | tomato, cucumber, feta, olives" → meal: salads\\ncuisine: mediterranean\n'
)

VALID_MEALS = set(MEAL_TYPES)
VALID_CUISINES = set(CUISINES)


def _parse_response(text: str) -> tuple[str, str]:
    """Parse LLM response into (meal_type, cuisine)."""
    text = text.strip().lower()
    meal = "mains"
    cuisine = "other"

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("meal:"):
            val = line[5:].strip()
            meal = val if val in VALID_MEALS else _fuzzy_match(val, MEAL_TYPES)
        elif line.startswith("cuisine:"):
            val = line[8:].strip()
            cuisine = val if val in VALID_CUISINES else _fuzzy_match(val, CUISINES)

    return meal, cuisine


def _fuzzy_match(val: str, options: list[str]) -> str:
    from rapidfuzz import fuzz
    best, best_score = options[-1], 0
    for opt in options:
        score = fuzz.ratio(val, opt)
        if score > best_score:
            best, best_score = opt, score
    return best if best_score >= 60 else options[-1]


def load_recipes() -> list[dict]:
    if not os.path.exists(RECIPES_FILE):
        print(f"No recipes file found at {RECIPES_FILE}")
        print("Run: python tools/migrate_recipes_to_json.py")
        sys.exit(1)
    with open(RECIPES_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("recipes", [])


def save_recipes(recipes: list[dict]):
    with open(RECIPES_FILE, "w", encoding="utf-8") as f:
        json.dump({"recipes": recipes}, f, ensure_ascii=False)


def run_categorization(url: str, model: str, force: bool = False):
    from openai import OpenAI
    from tqdm import tqdm

    client = OpenAI(base_url=url, api_key="not-needed")

    print("Loading recipes...")
    recipes = load_recipes()

    if force:
        remaining = list(range(len(recipes)))
    else:
        # Only categorize recipes without LLM-assigned cuisine
        remaining = [i for i, r in enumerate(recipes)
                     if not r.get("cuisine") or r.get("cuisine") == "other"]

    print(f"Total: {len(recipes)} | Need categorization: {len(remaining)}")

    if not remaining:
        print("All recipes already categorized!")
        show_stats(recipes)
        return

    errors = 0
    pbar = tqdm(remaining, desc="Categorizing", unit="recipe",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}")
    try:
        for count, idx in enumerate(pbar):
            recipe = recipes[idx]
            # Build ingredient list for context
            ings = recipe.get("ingredients", [])
            if isinstance(ings, list):
                ing_text = ", ".join(str(i) for i in ings[:8])
            else:
                ing_text = str(ings)[:200]

            try:
                user_msg = f"{recipe['title']} | {ing_text}"
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    max_tokens=30,
                    temperature=0,
                )
                meal, cuisine = _parse_response(response.choices[0].message.content)
                recipe["meal_type"] = meal
                recipe["cuisine"] = cuisine
                pbar.set_postfix_str(f"{recipe['title'][:20]} → {meal}/{cuisine}", refresh=False)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                errors += 1
                if errors <= 5:
                    tqdm.write(f"  Error on '{recipe['title'][:50]}': {e}")
                elif errors == 6:
                    tqdm.write("  (suppressing further errors)")

            if (count + 1) % CHECKPOINT_INTERVAL == 0:
                save_recipes(recipes)
    except KeyboardInterrupt:
        pbar.close()
        print(f"\nInterrupted. Saving...")
        save_recipes(recipes)
        print(f"Saved. {count + 1} processed. Re-run to continue (only uncategorized will be processed).")
        return

    pbar.close()
    save_recipes(recipes)
    print(f"Done! Categorized {len(remaining)} recipes ({errors} errors)")
    show_stats(recipes)


def show_stats(recipes: list[dict] = None):
    if recipes is None:
        recipes = load_recipes()

    from collections import Counter
    meals = Counter(r.get("meal_type", "unknown") for r in recipes)
    cuisines = Counter(r.get("cuisine", "unknown") for r in recipes)
    has_cuisine = sum(1 for r in recipes if r.get("cuisine") and r["cuisine"] != "other")

    print(f"\n{'='*50}")
    print(f"Recipe Category Statistics ({len(recipes)} recipes)")
    print(f"{'='*50}")
    print(f"With LLM cuisine: {has_cuisine}/{len(recipes)}")

    print(f"\nBy Meal Type:")
    for mt in MEAL_TYPES:
        count = meals.get(mt, 0)
        bar = "█" * (count // 50)
        print(f"  {mt:25s} {count:5d}  {bar}")
    if meals.get("unknown"):
        print(f"  {'unknown':25s} {meals['unknown']:5d}")

    print(f"\nBy Cuisine:")
    for c in CUISINES:
        count = cuisines.get(c, 0)
        bar = "█" * (count // 50)
        print(f"  {c:25s} {count:5d}  {bar}")
    if cuisines.get("unknown"):
        print(f"  {'unknown':25s} {cuisines['unknown']:5d}")


def main():
    parser = argparse.ArgumentParser(description="Categorize recipes by meal type and cuisine via LLM")
    parser.add_argument("--url", default="http://localhost:1234/v1", help="LLM API URL")
    parser.add_argument("--model", default=None, help="Model name (default: auto-detect)")
    parser.add_argument("--force", action="store_true", help="Re-categorize ALL recipes (not just uncategorized)")
    parser.add_argument("--stats", action="store_true", help="Show stats from existing data")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

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

    run_categorization(args.url, args.model, force=args.force)


if __name__ == "__main__":
    main()
