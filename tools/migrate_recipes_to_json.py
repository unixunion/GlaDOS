"""Migrate recipe dataset from CSV to JSON format, and manage categories.

One-time migration from CSV, then all future updates work on JSON directly.

Usage:
    python tools/migrate_recipes_to_json.py                # migrate CSV → JSON (first time only)
    python tools/migrate_recipes_to_json.py --merge-categories  # update categories in existing JSON
    python tools/migrate_recipes_to_json.py --stats        # show stats

Input:  data/recipes/dataset.csv (only for initial migration)
Output: plugin_data/recipes/recipes.json
"""
import argparse
import ast
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INPUT_CSV = "data/recipes/dataset.csv"
OUTPUT_JSON = "plugin_data/recipes/recipes.json"
CATEGORY_FILE = "plugin_data/recipes/recipe_categories.json"


def safe_parse_list(val):
    try:
        result = ast.literal_eval(val)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def split_instructions(text: str) -> list[str]:
    if not text:
        return []
    lines = text.split("\n")
    steps = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) > 200:
            sentences = re.split(r'(?<=[.!])\s+(?=[A-Z])', line)
            steps.extend(s.strip() for s in sentences if s.strip())
        else:
            steps.append(line)
    return steps


def migrate():
    """Initial migration: CSV → JSON. Only needed once."""
    if os.path.exists(OUTPUT_JSON):
        print(f"{OUTPUT_JSON} already exists.")
        print("Use --merge-categories to update categories, or delete the file to re-migrate.")
        return

    if not os.path.exists(INPUT_CSV):
        print(f"CSV not found: {INPUT_CSV}")
        sys.exit(1)

    print(f"Reading {INPUT_CSV}...")
    recipes = []
    with open(INPUT_CSV, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("Title", "").strip()
            if not title:
                continue
            recipes.append({
                "title": title,
                "ingredients": safe_parse_list(row.get("Ingredients", "[]")),
                "directions": split_instructions(row.get("Instructions", "")),
                "image_name": row.get("Image_Name", "").strip() or None,
                "source": "dataset",
            })

    print(f"Parsed {len(recipes)} recipes")
    _merge_categories(recipes)
    _save(recipes)


def merge_categories():
    """Update categories in existing recipes.json from recipe_categories.json."""
    if not os.path.exists(OUTPUT_JSON):
        print(f"{OUTPUT_JSON} not found. Run without flags to migrate first.")
        sys.exit(1)

    print(f"Loading {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    recipes = data.get("recipes", [])
    print(f"Loaded {len(recipes)} recipes")

    _merge_categories(recipes)
    _save(recipes)


def _merge_categories(recipes: list):
    """Merge LLM categories from recipe_categories.json into recipe list."""
    if not os.path.exists(CATEGORY_FILE):
        print(f"No category file found at {CATEGORY_FILE}")
        return

    with open(CATEGORY_FILE, "r") as f:
        cat_map = json.load(f)

    merged = 0
    for recipe in recipes:
        entry = cat_map.get(recipe["title"])
        if isinstance(entry, dict):
            recipe["meal_type"] = entry.get("meal_type")
            recipe["cuisine"] = entry.get("cuisine")
            merged += 1
        elif isinstance(entry, str):
            recipe["meal_type"] = entry
            merged += 1
    print(f"Merged categories for {merged}/{len(recipes)} recipes")


def _save(recipes: list):
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"recipes": recipes}, f, ensure_ascii=False)
    size_mb = os.path.getsize(OUTPUT_JSON) / 1024 / 1024
    print(f"Written to {OUTPUT_JSON} ({size_mb:.1f} MB)")


def show_stats():
    if not os.path.exists(OUTPUT_JSON):
        print(f"No JSON file found at {OUTPUT_JSON}")
        if os.path.exists(INPUT_CSV):
            lines = sum(1 for _ in open(INPUT_CSV)) - 1
            print(f"CSV has ~{lines} rows. Run without flags to migrate.")
        return

    with open(OUTPUT_JSON, "r") as f:
        data = json.load(f)
    recipes = data.get("recipes", [])
    size_mb = os.path.getsize(OUTPUT_JSON) / 1024 / 1024

    from collections import Counter
    sources = Counter(r.get("source", "unknown") for r in recipes)
    meals = Counter(r.get("meal_type", "unknown") for r in recipes)
    cuisines = Counter(r.get("cuisine", "unknown") for r in recipes)
    has_categories = sum(1 for r in recipes if r.get("cuisine") and r["cuisine"] != "other")

    print(f"Recipes: {len(recipes)} ({size_mb:.1f} MB)")
    print(f"Sources: {dict(sources)}")
    print(f"With LLM categories: {has_categories}/{len(recipes)}")
    print(f"\nBy Meal Type:")
    for mt, n in meals.most_common():
        print(f"  {mt:25s} {n}")
    print(f"\nBy Cuisine:")
    for c, n in cuisines.most_common():
        print(f"  {c:25s} {n}")


def main():
    parser = argparse.ArgumentParser(description="Migrate recipes CSV → JSON and manage categories")
    parser.add_argument("--stats", action="store_true", help="Show stats only")
    parser.add_argument("--merge-categories", action="store_true",
                        help="Update categories in existing recipes.json from recipe_categories.json")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.merge_categories:
        merge_categories()
    else:
        migrate()


if __name__ == "__main__":
    main()
