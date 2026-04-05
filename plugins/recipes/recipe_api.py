import ast
import json
import os
import re
from collections import defaultdict
from typing import List, Dict, Any

from loguru import logger
from rapidfuzz import fuzz, process

from glados.context.activity import Activity
from glados.system.event_system import EventSystem, EventMessage
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem

event_system = EventSystem()
plugin_manager = PluginSystem()
plugin_manager.register_system_prompt(
    "When selecting a recipe, interpret the user's selection based on prior results "
    "and proceed without restarting the search unless explicitly instructed to search again."
)

# Global variables
data_file = "data/recipes/dataset.csv"
INGREDIENT_MAP_FILE = "plugin_data/recipes/ingredient_map.json"
RECIPES_JSON_FILE = "plugin_data/recipes/recipes.json"
USER_RECIPES_FILE = "plugin_data/recipes/user_recipes.json"

recipes = []
ingredient_set = set()
# Reverse index: ingredient word → set of recipe indices (for fast ingredient search)
ingredient_index = defaultdict(set)
# Ingredient normalization: cleaned ingredient → core name
ingredient_map: dict[str, str] = {}
# All unique core ingredient names (for normalize_ingredient lookups)
core_ingredient_set: set[str] = set()
# Sorted list for rapidfuzz process.extract (built once from core_ingredient_set)
_core_ingredient_list: list[str] = []
# Category index: category name → list of recipe indices
category_index: dict[str, list[int]] = {}

# Recipe category inference from title keywords
RECIPE_CATEGORIES = {
    "breakfast": ["pancake", "waffle", "omelette", "omelet", "french toast", "oats", "oatmeal",
                  "granola", "scrambled", "frittata", "breakfast", "brunch", "eggs benedict",
                  "porridge", "cereal", "crepe", "hash brown"],
    "soups": ["soup", "stew", "chowder", "bisque", "broth", "goulash", "chili", "gumbo", "pho",
              "ramen", "minestrone", "gazpacho", "bouillabaisse"],
    "salads": ["salad", "slaw", "coleslaw", "tabbouleh", "fattoush"],
    "pasta": ["pasta", "spaghetti", "penne", "fettuccine", "linguine", "ravioli", "lasagna",
              "macaroni", "noodle", "carbonara", "bolognese", "alfredo", "rigatoni", "orzo",
              "gnocchi", "tortellini"],
    "baking": ["cake", "cookie", "brownie", "pie", "tart", "cupcake", "scone", "pastry",
               "biscuit", "donut", "doughnut", "croissant", "cheesecake", "truffle",
               "fudge", "praline", "meringue", "pudding", "mousse", "tiramisu",
               "cobbler", "crisp", "crumble", "semifreddo"],
    "bread": ["bread", "roll", "baguette", "focaccia", "pretzel", "cornbread", "flatbread",
              "pita", "naan", "brioche", "sourdough"],
    "drinks": ["smoothie", "cocktail", "margarita", "lemonade", "punch", "shake", "juice",
               "sangria", "mojito", "daiquiri", "eggnog", "cider", "tea ", "iced tea"],
    "appetizers": ["dip", "hummus", "guacamole", "bruschetta", "crostini", "appetizer",
                   "wings", "spring roll", "empanada", "quesadilla", "nachos", "poppers"],
    "seafood": ["salmon", "shrimp", "tuna", "fish", "crab", "lobster", "scallop", "clam",
                "mussel", "oyster", "cod", "halibut", "swordfish", "tilapia", "prawn",
                "calamari", "anchov"],
    "chicken": ["chicken", "turkey", "poultry", "hen"],
    "beef": ["beef", "steak", "burger", "meatball", "meatloaf", "brisket", "pot roast",
             "short rib", "prime rib"],
    "pork": ["pork", "bacon", "ham", "sausage", "chorizo", "prosciutto", "pulled pork",
             "ribs", "carnitas"],
    "vegetarian": ["tofu", "tempeh", "vegetarian", "vegan", "veggie"],
    "sides": ["couscous", "quinoa", "polenta", "gratin", "grits"],
}

CATEGORY_ICONS = {
    "breakfast": "sunrise", "soups": "flame", "soups & stews": "flame",
    "salads": "leaf", "pasta": "utensils", "pasta & noodles": "utensils",
    "baking": "cookie", "baking & desserts": "cookie",
    "bread": "sandwich", "drinks": "wine", "drinks & cocktails": "wine",
    "appetizers": "carrot", "appetizers & snacks": "carrot",
    "seafood": "fish", "chicken": "beef", "beef": "beef", "pork": "beef",
    "vegetarian": "leaf", "sides": "utensils",
    "sauces & condiments": "flame",
    "mediterranean": "leaf", "asian": "utensils",
    "mexican & latin": "flame", "indian": "flame",
    "middle eastern": "leaf", "american comfort": "beef",
    "other": "chef-hat",
}

CATEGORY_ORDER = [
    "breakfast", "soups", "soups & stews", "salads",
    "pasta", "pasta & noodles", "chicken", "beef", "pork", "seafood",
    "vegetarian", "mediterranean", "asian", "indian",
    "mexican & latin", "middle eastern", "american comfort",
    "baking", "baking & desserts", "bread",
    "appetizers", "appetizers & snacks", "sides",
    "sauces & condiments", "drinks", "drinks & cocktails", "other",
]


def _categorize_recipe(title: str) -> str:
    """Infer recipe category from title keywords."""
    title_lower = title.lower()
    for cat, keywords in RECIPE_CATEGORIES.items():
        for kw in keywords:
            if kw in title_lower:
                return cat
    return "other"


def _build_category_index():
    """Build reverse index from category/cuisine → recipe indices."""
    global category_index
    idx = defaultdict(list)
    for i, recipe in enumerate(recipes):
        # Index by meal_type
        meal = recipe.get("meal_type", recipe.get("category", "other"))
        idx[f"meal:{meal}"].append(i)
        # Index by cuisine (if available)
        cuisine = recipe.get("cuisine")
        if cuisine:
            idx[f"cuisine:{cuisine}"].append(i)
    category_index = dict(idx)


MEAL_TYPE_ORDER = [
    "breakfast", "appetizers & snacks", "mains", "sides",
    "soups & stews", "soups", "salads", "baking & desserts", "baking",
    "bread", "sauces & condiments", "drinks", "drinks & cocktails", "other",
]

CUISINE_ORDER = [
    "american", "italian", "mexican & latin", "asian", "indian",
    "mediterranean", "middle eastern", "french", "british", "other",
]

MEAL_ICONS = {
    "breakfast": "sunrise", "appetizers & snacks": "carrot", "appetizers": "carrot",
    "mains": "utensils", "sides": "utensils",
    "soups & stews": "flame", "soups": "flame", "salads": "leaf",
    "baking & desserts": "cookie", "baking": "cookie",
    "bread": "sandwich", "sauces & condiments": "flame",
    "drinks": "wine", "drinks & cocktails": "wine", "other": "chef-hat",
}

CUISINE_ICONS = {
    "american": "flag", "italian": "utensils", "mexican & latin": "flame",
    "asian": "utensils", "indian": "flame", "mediterranean": "leaf",
    "middle eastern": "leaf", "french": "wine", "british": "beef", "other": "chef-hat",
}


def get_categories() -> dict:
    """Get categories grouped by meal type and cuisine, with counts."""
    meal_types = []
    for mt in MEAL_TYPE_ORDER:
        indices = category_index.get(f"meal:{mt}", [])
        if indices:
            meal_types.append({
                "name": mt, "count": len(indices),
                "icon": MEAL_ICONS.get(mt, "chef-hat"),
            })

    cuisines = []
    for c in CUISINE_ORDER:
        indices = category_index.get(f"cuisine:{c}", [])
        if indices:
            cuisines.append({
                "name": c, "count": len(indices),
                "icon": CUISINE_ICONS.get(c, "chef-hat"),
            })

    return {"meal_types": meal_types, "cuisines": cuisines}


def get_recipes_by_category(category: str, offset: int = 0, limit: int = 20) -> list[dict]:
    """Get paginated recipes for a category. Category can be 'meal:X' or 'cuisine:X'."""
    # Support both prefixed and unprefixed lookups
    if not category.startswith(("meal:", "cuisine:")):
        indices = category_index.get(f"meal:{category}", []) or category_index.get(f"cuisine:{category}", [])
    else:
        indices = category_index.get(category, [])
    page = indices[offset:offset + limit]
    pantry_names = _get_pantry_names()
    pantry_set = set(pantry_names)
    result = []
    for idx in page:
        recipe = recipes[idx]
        core = recipe.get("core_ingredients", [])
        have = sum(1 for c in core if c in pantry_set) if pantry_set else 0
        result.append({
            "index": idx,
            "title": recipe["title"],
            "image_name": recipe.get("image_name"),
            "ingredient_count": len(core),
            "have_count": have,
            "meal_type": recipe.get("meal_type", recipe.get("category", "other")),
            "cuisine": recipe.get("cuisine", ""),
        })
    return result



def _load_ingredient_map() -> str:
    """Load ingredient_map.json for ingredient normalization."""
    global ingredient_map, core_ingredient_set, _core_ingredient_list
    if not os.path.exists(INGREDIENT_MAP_FILE):
        logger.info("No ingredient map found — normalization disabled")
        return
    with open(INGREDIENT_MAP_FILE, "r") as f:
        ingredient_map = json.load(f)
    core_ingredient_set = set(ingredient_map.values())
    _core_ingredient_list = sorted(core_ingredient_set)
    logger.info(f"Loaded ingredient map: {len(ingredient_map)} mappings → {len(core_ingredient_set)} core ingredients")


def _load_from_json(json_path: str):
    """Load recipes from JSON file and process them."""
    global recipes, ingredient_set, ingredient_index

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_recipes = data.get("recipes", [])
    recipes = []
    ingredient_set = set()
    ingredient_index = defaultdict(set)

    for raw in raw_recipes:
        title = raw.get("title", "").strip()
        if not title:
            continue

        raw_ingredients = raw.get("ingredients", [])
        directions = raw.get("directions", [])
        image_name = raw.get("image_name")

        cleaned = clean_ingredients(raw_ingredients)
        ingredient_set.update(cleaned)
        core = [ingredient_map.get(c, c) for c in cleaned]

        # Use stored categories if present, otherwise keyword-infer
        meal_type = raw.get("meal_type") or _categorize_recipe(title)
        cuisine = raw.get("cuisine", "other")

        recipe_idx = len(recipes)
        recipes.append({
            "title": title,
            "ingredients": raw_ingredients,
            "directions": directions,
            "cleaned_ingredients": cleaned,
            "core_ingredients": core,
            "image_name": image_name,
            "category": meal_type,
            "meal_type": meal_type,
            "cuisine": cuisine,
            "source": raw.get("source", "dataset"),
        })

        for ing in core:
            for word in ing.split():
                if len(word) > 2:
                    ingredient_index[word].add(recipe_idx)

    logger.info(f"Processed {len(recipes)} recipes from JSON")


def _auto_migrate_csv_to_json(csv_path: str):
    """Auto-migrate CSV to JSON on first startup if JSON doesn't exist."""
    logger.info(f"Auto-migrating CSV to JSON: {csv_path} → {RECIPES_JSON_FILE}")
    import csv as csv_mod

    raw_recipes = []
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            title = row.get("Title", "").strip()
            if not title:
                continue
            raw_recipes.append({
                "title": title,
                "ingredients": safe_parse_list(row.get("Ingredients", "[]")),
                "directions": _split_instructions(row.get("Instructions", "")),
                "image_name": row.get("Image_Name", "").strip() or None,
                "source": "dataset",
            })

    os.makedirs(os.path.dirname(RECIPES_JSON_FILE), exist_ok=True)
    with open(RECIPES_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump({"recipes": raw_recipes}, f, ensure_ascii=False)

    logger.success(f"Migrated {len(raw_recipes)} recipes to {RECIPES_JSON_FILE}")


def _load_user_recipes():
    """Load user-added recipes from JSON and append to the recipes list."""
    if not os.path.exists(USER_RECIPES_FILE):
        return 0

    try:
        with open(USER_RECIPES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load user recipes: {e}")
        return 0

    user_recipes = data.get("recipes", [])
    added = 0
    for raw in user_recipes:
        title = raw.get("title", "").strip()
        if not title:
            continue

        raw_ingredients = raw.get("ingredients", [])
        directions = raw.get("directions", [])
        cleaned = clean_ingredients(raw_ingredients)
        ingredient_set.update(cleaned)
        core = [ingredient_map.get(c, c) for c in cleaned]

        meal_type = raw.get("meal_type") or _categorize_recipe(title)
        cuisine = raw.get("cuisine", "other")

        recipe_idx = len(recipes)
        recipes.append({
            "title": title,
            "ingredients": raw_ingredients,
            "directions": directions,
            "cleaned_ingredients": cleaned,
            "core_ingredients": core,
            "image_name": raw.get("image_name"),
            "category": meal_type,
            "meal_type": meal_type,
            "cuisine": cuisine,
            "source": "user",
            "description": raw.get("description"),
            "servings": raw.get("servings", 4),
            "added": raw.get("added"),
        })

        for ing in core:
            for word in ing.split():
                if len(word) > 2:
                    ingredient_index[word].add(recipe_idx)
        added += 1

    if added:
        logger.info(f"Loaded {added} user recipes from {USER_RECIPES_FILE}")
    return added


def _save_dataset_recipes():
    """Save dataset recipes back to JSON (with updated categories)."""
    dataset_recipes = [r for r in recipes if r.get("source") != "user"]
    data = {
        "recipes": [
            {
                "title": r["title"],
                "ingredients": r["ingredients"],
                "directions": r["directions"],
                "image_name": r.get("image_name"),
                "meal_type": r.get("meal_type"),
                "cuisine": r.get("cuisine"),
                "source": "dataset",
            }
            for r in dataset_recipes
        ]
    }
    os.makedirs(os.path.dirname(RECIPES_JSON_FILE), exist_ok=True)
    with open(RECIPES_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info(f"[Recipe] Saved {len(dataset_recipes)} dataset recipes to {RECIPES_JSON_FILE}")


def _save_user_recipes():
    """Save user-added recipes to JSON."""
    user_recipes = [r for r in recipes if r.get("source") == "user"]
    data = {
        "recipes": [
            {
                "title": r["title"],
                "ingredients": r["ingredients"],
                "directions": r["directions"],
                "image_name": r.get("image_name"),
                "meal_type": r.get("meal_type"),
                "cuisine": r.get("cuisine"),
                "source": "user",
                "description": r.get("description"),
                "servings": r.get("servings", 4),
                "added": r.get("added"),
            }
            for r in user_recipes
        ]
    }
    os.makedirs(os.path.dirname(USER_RECIPES_FILE), exist_ok=True)
    with open(USER_RECIPES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_recipes(file_path: str):
    """Load recipes from JSON (primary) or CSV (fallback with auto-migration)."""
    global recipes, ingredient_set, ingredient_index

    _load_ingredient_map()

    # Primary: load from JSON
    if os.path.exists(RECIPES_JSON_FILE):
        logger.info(f"Loading recipes from JSON: {RECIPES_JSON_FILE}")
        _load_from_json(RECIPES_JSON_FILE)
    elif os.path.exists(file_path):
        # Fallback: CSV with auto-migration to JSON
        logger.info(f"No JSON found — migrating from CSV: {file_path}")
        _auto_migrate_csv_to_json(file_path)
        _load_from_json(RECIPES_JSON_FILE)
    else:
        logger.warning(f"No recipe data found ({RECIPES_JSON_FILE} or {file_path})")
        return

    # Load user recipes
    _load_user_recipes()

    # Build indices
    if not ingredient_index and recipes:
        _build_ingredient_index()
    if recipes:
        _build_category_index()

    logger.success(f"Loaded {len(recipes)} recipes with {len(ingredient_set)} unique ingredients, {len(category_index)} categories.")
    event_system.publish(EventMessage(
        "tool", "plugin_system",
        f"The recipe system has loaded {len(recipes)} recipes.",
        process_output=False,
    ))

    # Start Qdrant recipe indexing in background (if enabled)
    try:
        from plugins.recipes.recipe_qdrant import start_background
        start_background()
    except ImportError:
        pass

    # Start delta categorization worker for uncategorized recipes
    _start_delta_worker()


def _start_delta_worker():
    """Start background worker to categorize/extract uncategorized recipes via LLM."""
    import threading

    def _worker():
        import time
        # Wait for startup to complete — avoid competing with power-on greeting
        # and other LLM calls during boot
        time.sleep(30)
        try:
            _run_delta_categorization()
            _run_delta_ingredient_extraction()
        except Exception as e:
            logger.debug(f"[Recipe] Delta worker error: {e}")

    threading.Thread(target=_worker, daemon=True, name="recipe-delta-worker").start()


def _run_delta_categorization():
    """Categorize recipes that still have keyword-inferred or missing cuisine via LLM."""
    # Find recipes without LLM-assigned cuisine (still "other" from keyword fallback)
    uncategorized = [r for r in recipes if r.get("cuisine", "other") == "other"
                     and r.get("meal_type") == r.get("category")]
    if not uncategorized:
        return

    logger.info(f"[Recipe] Delta categorization: {len(uncategorized)} uncategorized recipes")

    try:
        from glados.config import GladosConfig
        config = GladosConfig.from_yaml("glados_config.yml")
        from openai import OpenAI
        classify_model = getattr(config, "recipe_classify_model", None) or config.model
        client = OpenAI(base_url=config.completion_url, api_key=config.api_key or "not-needed")
        logger.info(f"[Recipe] Delta categorization using model: {classify_model}")
    except Exception as e:
        logger.debug(f"[Recipe] Delta categorization skipped — no LLM available: {e}")
        return

    from tools.categorize_recipes import SYSTEM_PROMPT, _parse_response

    updated = 0
    dataset_dirty = False
    user_dirty = False
    for recipe in uncategorized[:50]:  # Limit per startup to avoid long blocking
        try:
            user_msg = f"{recipe['title']} | {', '.join(recipe.get('core_ingredients', [])[:8])}"
            response = client.chat.completions.create(
                model=classify_model,
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
            recipe["category"] = meal
            updated += 1
            if recipe.get("source") == "user":
                user_dirty = True
            else:
                dataset_dirty = True
        except Exception as e:
            logger.debug(f"[Recipe] Delta categorize failed for '{recipe['title'][:30]}': {e}")
            break  # LLM likely unavailable

    if updated:
        if dataset_dirty:
            _save_dataset_recipes()
        if user_dirty:
            _save_user_recipes()
        _build_category_index()
        logger.info(f"[Recipe] Delta categorized {updated} recipes")


def _run_delta_ingredient_extraction():
    """Extract core names for ingredients not yet in ingredient_map.json."""
    if not ingredient_map:
        return

    # Find unknown cleaned ingredients
    unknown = set()
    for recipe in recipes:
        for c in recipe.get("cleaned_ingredients", []):
            if c not in ingredient_map:
                unknown.add(c)

    if not unknown:
        return

    logger.info(f"[Recipe] Delta ingredient extraction: {len(unknown)} unknown ingredients")

    try:
        from glados.config import GladosConfig
        config = GladosConfig.from_yaml("glados_config.yml")
        from openai import OpenAI
        classify_model = getattr(config, "recipe_classify_model", None) or config.model
        client = OpenAI(base_url=config.completion_url, api_key=config.api_key or "not-needed")
    except Exception:
        return

    from tools.extract_core_ingredients import SYSTEM_PROMPT as ING_PROMPT

    updated = 0
    for ing in list(unknown)[:50]:  # Limit to 50 per startup to avoid long blocking
        try:
            response = client.chat.completions.create(
                model=classify_model,
                messages=[
                    {"role": "system", "content": ING_PROMPT},
                    {"role": "user", "content": ing},
                ],
                max_tokens=20,
                temperature=0,
            )
            core = response.choices[0].message.content.strip().lower()
            ingredient_map[ing] = core
            updated += 1
        except Exception:
            break  # LLM unavailable, stop trying

    if updated:
        with open(INGREDIENT_MAP_FILE, "w") as f:
            json.dump(ingredient_map, f, indent=2, ensure_ascii=False)
        # Update core_ingredient_set
        global core_ingredient_set, _core_ingredient_list
        core_ingredient_set = set(ingredient_map.values())
        _core_ingredient_list = sorted(core_ingredient_set)
        logger.info(f"[Recipe] Delta extracted {updated} ingredient mappings")


def _load_from_csv(file_path: str):
    """Parse the recipe CSV into the global recipes list."""
    import csv

    global recipes, ingredient_set, ingredient_index

    recipes = []
    ingredient_set = set()
    ingredient_index = defaultdict(set)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            try:
                title = row.get("Title", "").strip()
                if not title:
                    continue

                # Parse ingredients (Python list literal)
                raw_ingredients = safe_parse_list(row.get("Ingredients", "[]"))

                # Parse instructions — split paragraph into steps
                raw_instructions = row.get("Instructions", "")
                directions = _split_instructions(raw_instructions)

                # Clean ingredients for search (strip quantities, units, parentheticals)
                cleaned = clean_ingredients(raw_ingredients)
                ingredient_set.update(cleaned)

                # Map cleaned ingredients to core names via ingredient_map
                core = [ingredient_map.get(c, c) for c in cleaned]

                # Image name (maps to data/recipes/img/Food Images/{name}.jpg)
                image_name = row.get("Image_Name", "").strip() or None

                recipe_idx = len(recipes)
                recipes.append({
                    "title": title,
                    "ingredients": raw_ingredients,
                    "directions": directions,
                    "cleaned_ingredients": cleaned,
                    "core_ingredients": core,
                    "image_name": image_name,
                    "category": _categorize_recipe(title),
                })

                # Build ingredient word index from core ingredients
                for ing in core:
                    for word in ing.split():
                        if len(word) > 2:
                            ingredient_index[word].add(recipe_idx)

                count += 1
                if count % 10000 == 0:
                    logger.info(f"Processed {count} recipes...")
            except Exception as e:
                logger.debug(f"Skipping row: {e}")

    logger.info(f"Processed {count} recipes from CSV")


def _split_instructions(text: str) -> list[str]:
    """Split instruction text into individual steps."""
    if not text:
        return []
    # Split on newlines first
    lines = text.split("\n")
    steps = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # If very long (>200 chars), try splitting on sentence boundaries
        if len(line) > 200:
            sentences = re.split(r'(?<=[.!])\s+(?=[A-Z])', line)
            steps.extend(s.strip() for s in sentences if s.strip())
        else:
            steps.append(line)
    return steps


def _build_ingredient_index():
    """Build reverse index from ingredient words to recipe indices."""
    global ingredient_index
    ingredient_index = defaultdict(set)
    for i, recipe in enumerate(recipes):
        for ing in recipe.get("core_ingredients", recipe.get("cleaned_ingredients", [])):
            for word in ing.split():
                if len(word) > 2:
                    ingredient_index[word].add(i)


def clean_ingredients(raw_ingredients: List[str]) -> List[str]:
    """Clean and normalize ingredient names — strip quantities, units, parentheticals."""
    cleaned = []
    for ingredient in raw_ingredients:
        # Remove parentheticals, quantities, units
        ing = re.sub(r"\(.*?\)", "", ingredient)
        ing = re.sub(r"^\s*\d[\d\s½¼¾⅓⅔⅛/.\-]*\s*", "", ing)
        ing = re.sub(
            r"^(?:tsp|tsps|tbsp|tbsps|cup|cups|oz|ounce|ounces|lb|lbs|pound|pounds|"
            r"gallon|quart|pint|pinch|dash|can|cans|package|pkg|bunch|clove|cloves|"
            r"slice|slices|piece|pieces|head|stalk|stalks|sprig|sprigs|"
            r"small|medium|large|extra)s?\b\.?\s*",
            "", ing, flags=re.IGNORECASE
        )
        ing = re.sub(r"\s+", " ", ing).strip().lower()
        # Remove trailing punctuation and common suffixes
        ing = re.sub(r"[,;]+$", "", ing).strip()
        if ing and len(ing) > 1:
            cleaned.append(ing)
    return cleaned


def search_by_ingredients(query_ingredients: List[str]) -> List[Dict]:
    """Search recipes by ingredient matching.

    Tries Qdrant semantic search first (if enabled and ready), falls back
    to word-index + fuzzy matching.
    """
    if not query_ingredients:
        return []

    # Try Qdrant semantic search first
    try:
        from plugins.recipes.recipe_qdrant import search_recipes_semantic
        results = search_recipes_semantic(query_ingredients)
        if results is not None:
            return results
    except ImportError:
        pass

    # Fallback: word-index + fuzzy matching
    candidate_indices = set()
    for q_ing in query_ingredients:
        for word in q_ing.lower().split():
            if word in ingredient_index:
                candidate_indices.update(ingredient_index[word])

    matching_recipes = []
    for idx in candidate_indices:
        recipe = recipes[idx]
        recipe_ings = recipe.get("core_ingredients", recipe.get("cleaned_ingredients", []))
        matched = []
        for q_ing in query_ingredients:
            q_lower = q_ing.lower()
            for r_ing in recipe_ings:
                if q_lower == r_ing or fuzz.partial_ratio(q_lower, r_ing) >= 75:
                    matched.append(q_ing)
                    break
        if matched:
            matching_recipes.append({
                "title": recipe["title"],
                "ingredients": recipe["ingredients"],
                "directions": recipe["directions"],
                "image_name": recipe.get("image_name"),
                "matched_ingredients": matched,
                "match_count": len(matched),
                "search_method": "fuzzy",
            })

    return sorted(matching_recipes, key=lambda x: x["match_count"], reverse=True)


def convert_fractions(text: str) -> str:
    """Converts fractions and mixed numbers to TTS-friendly spoken forms."""
    fraction_map = {
        "1/2": "one half", "1/3": "one third", "2/3": "two thirds",
        "1/4": "one quarter", "3/4": "three quarters",
        "1/8": "one eighth", "3/8": "three eighths",
        "5/8": "five eighths", "7/8": "seven eighths",
    }

    def replace_mixed(match):
        whole = match.group(1)
        frac = match.group(2)
        spoken_frac = fraction_map.get(frac)
        if spoken_frac:
            return f"{whole} and {spoken_frac}"
        num, den = frac.split("/")
        return f"{whole} and {num} over {den}"

    text = re.sub(r"(\d+)\s+(\d+/\d+)", replace_mixed, text)

    def replace_fraction(match):
        frac = match.group(0)
        if frac in fraction_map:
            return fraction_map[frac]
        num, den = frac.split("/")
        return f"{num} over {den}"

    text = re.sub(r"\d+/\d+", replace_fraction, text)
    return text


def convert_abbreviations(text: str) -> str:
    """Converts common cooking abbreviations to full forms for clarity."""
    abbreviation_map = {
        "tsp": "teaspoon", "tsps": "teaspoons",
        "tbl": "tablespoon", "tbls": "tablespoons",
        "tbsp": "tablespoon", "tbsps": "tablespoons",
        "oz": "ounce", "ozs": "ounces",
        "c": "cup", "qt": "quart", "qts": "quarts",
        "pt": "pint", "pts": "pints",
        "pkg": "package", "pkgs": "packages",
        "lb": "pound", "lbs": "pounds",
        "gal": "gallon", "lg": "large", "sm": "small", "med": "medium",
    }
    words = text.split()
    return " ".join([abbreviation_map.get(word.lower(), word) for word in words])


# --- Metric annotation ---

# Imperial → metric conversion factors: (unit_pattern, metric_unit, factor, rounding)
_METRIC_CONVERSIONS = [
    # Volume
    (r"\bcups?\b", "ml", 237, 5),
    (r"\btablespoons?\b", "ml", 15, 1),
    (r"\bteaspoons?\b", "ml", 5, 1),
    (r"\bfluid\s+ounces?\b", "ml", 30, 5),
    (r"\bpints?\b", "ml", 473, 5),
    (r"\bquarts?\b", "L", 0.946, 0.1),
    (r"\bgallons?\b", "L", 3.785, 0.1),
    # Weight
    (r"\bpounds?\b", "g", 454, 5),
    (r"\blbs?\b", "g", 454, 5),
    (r"\bounces?\b", "g", 28.35, 1),
    (r"\boz\b", "g", 28.35, 1),
]

# Temperature: match "350°F", "350 degrees F", "350 degrees fahrenheit"
_TEMP_F_PATTERN = re.compile(
    r"(\d+)\s*(?:°\s*F|degrees?\s+fahrenheit|degrees?\s+F)\b",
    re.IGNORECASE,
)

# Number at start of measurement: "2 cups", "1/2 cup", "one and a half cups"
_MEASUREMENT_PATTERN = re.compile(
    r"([\d]+(?:\.\d+)?(?:\s+[\d/]+)?)\s+({unit})",
    re.IGNORECASE,
)


def _round_to(value: float, step: float) -> float:
    """Round to nearest step value."""
    if step >= 1:
        return round(value / step) * step
    return round(value / step) * step


def annotate_metric(text: str) -> str:
    """Add metric annotations to imperial measurements in recipe text.

    '2 cups flour' → '2 cups (~475ml) flour'
    '350°F' → '350°F (~175°C)'
    """
    # Temperature conversion
    def _convert_temp(m):
        f = int(m.group(1))
        c = round((f - 32) * 5 / 9 / 5) * 5  # Round to nearest 5°C
        return f"{m.group(0)} (~{c}°C)"

    text = _TEMP_F_PATTERN.sub(_convert_temp, text)

    # Volume/weight conversions
    for unit_pattern, metric_unit, factor, rounding in _METRIC_CONVERSIONS:
        pattern = re.compile(
            r"([\d]+(?:[./][\d]+)?(?:\s+[\d/]+)?)\s+(" + unit_pattern + r")",
            re.IGNORECASE,
        )

        def _convert_measurement(m, _factor=factor, _unit=metric_unit, _round=rounding):
            num_str = m.group(1).strip()
            # Parse number: handle "1/2", "1 1/2", "2.5"
            try:
                if "/" in num_str:
                    parts = num_str.split()
                    if len(parts) == 2:  # "1 1/2"
                        whole = float(parts[0])
                        n, d = parts[1].split("/")
                        num = whole + float(n) / float(d)
                    else:  # "1/2"
                        n, d = num_str.split("/")
                        num = float(n) / float(d)
                else:
                    num = float(num_str)
            except (ValueError, ZeroDivisionError):
                return m.group(0)

            metric_val = num * _factor
            metric_val = _round_to(metric_val, _round)

            # Format: use kg for >= 1000g, L for >= 1000ml
            if _unit == "g" and metric_val >= 1000:
                formatted = f"~{metric_val / 1000:.1f}kg"
            elif _unit == "ml" and metric_val >= 1000:
                formatted = f"~{metric_val / 1000:.1f}L"
            elif metric_val == int(metric_val):
                formatted = f"~{int(metric_val)}{_unit}"
            else:
                formatted = f"~{metric_val:.1f}{_unit}"

            return f"{m.group(0)} ({formatted})"

        text = pattern.sub(_convert_measurement, text)

    return text


# Load metric_annotations config once at module level
_metric_annotations_enabled = False
try:
    from glados.config import GladosConfig as _GC
    _metric_annotations_enabled = getattr(_GC.from_yaml("glados_config.yml"), "metric_annotations", False)
except Exception:
    pass


def format_ingredient_for_speech(ingredient: str) -> str:
    """Applies fraction conversion, abbreviation expansion, and optional metric annotations."""
    result = convert_abbreviations(convert_fractions(ingredient))
    if _metric_annotations_enabled:
        result = annotate_metric(result)
    return result


def safe_parse_list(serialized_list: Any) -> list:
    """Safely parses a serialized list string into a Python list."""
    if isinstance(serialized_list, list):
        return serialized_list
    try:
        return ast.literal_eval(serialized_list)
    except (ValueError, SyntaxError):
        logger.debug(f"Failed to parse list: {str(serialized_list)[:80]}")
        return []


def _get_pantry_names() -> list[str]:
    """Get lowercased pantry item names for ingredient cross-reference."""
    try:
        from plugins.pantry.pantry_plugin import PantryPlugin
        return [i["name"].lower() for i in PantryPlugin()._pantry["items"]]
    except Exception:
        return []


def _count_pantry_matches(ingredients: list[str], pantry_names: list[str]) -> tuple[int, int]:
    """Count how many ingredients are in the pantry. Returns (have, missing)."""
    have = 0
    for ing in ingredients:
        # Map ingredient to core name for better matching
        ing_lower = ing.lower()
        core = ingredient_map.get(ing_lower, ing_lower)
        if any(
            pn == core or fuzz.partial_ratio(pn, core) >= 75
            for pn in pantry_names
        ):
            have += 1
    return have, len(ingredients) - have


def normalize_ingredient(name: str) -> tuple[str, float]:
    """Normalize a user-provided ingredient name to a known core ingredient.

    Returns (normalized_name, confidence) where confidence is 0.0-1.0.
    If no good match, returns the original name with confidence 0.0.
    """
    name_lower = name.lower().strip()
    if not name_lower:
        return name_lower, 0.0

    # Direct lookup in ingredient map (cleaned ingredient → core)
    if name_lower in ingredient_map:
        return ingredient_map[name_lower], 1.0

    # Exact match in core ingredient set
    if name_lower in core_ingredient_set:
        return name_lower, 1.0

    # Fuzzy match against known core ingredients
    if _core_ingredient_list:
        result = process.extractOne(name_lower, _core_ingredient_list, scorer=fuzz.ratio)
        if result and result[1] >= 85:
            return result[0], result[1] / 100.0

    return name_lower, 0.0


def get_ingredient_suggestions(partial: str, limit: int = 5) -> list[str]:
    """Return top ingredient name suggestions for autocomplete."""
    if not partial or not _core_ingredient_list:
        return []
    results = process.extract(partial.lower().strip(), _core_ingredient_list, scorer=fuzz.partial_ratio, limit=limit)
    return [r[0] for r in results if r[1] >= 50]


# Last search results for positional selection ("select the first one")
_last_search_results = []

# Last selected recipe data (for programmatic access by add_recipe_ingredients_to_list)
_last_selected_recipe = None


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Search for recipes by keyword. Returns a list of matching recipes. Use this when the user wants to FIND, LOOK UP, or BROWSE recipes. Do NOT use this to select or start cooking a recipe — use select_recipe instead.",
                                             parameters=Parameters(type="object", required=['query'], properties={
                                                 'query': ParameterType(type="string",
                                                                        description="the recipe name to search for")
                                             })
                                         )
                                         ),
    intents=[
        "find me a recipe for bread",
        "search recipes for chili con carne",
        "what recipes for pizza do you know",
        "search for a recipe",
        "look up a recipe for cookies",
        "recipe search for lasagna",
        "find a recipe for soup",
        "do you have a recipe for cake",
        "search recipes for pecan pralines",
        "recipe for tacos",
        "find recipes for dinner",
        "look up recipes",
        "I need a recipe",
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def search_recipes(query: str) -> dict:
    """Search for recipes matching the query."""
    global _last_search_results
    logger.info(f"Recipe search query: {query}")

    matches = []
    for recipe in recipes:
        score = fuzz.partial_ratio(query.lower(), recipe['title'].lower())
        if score > 50:
            matches.append((score, recipe))

    matches = sorted(matches, key=lambda x: x[0], reverse=True)

    if not matches:
        logger.warning(f"No recipes found for: {query}")
        return "No recipes found matching the enquiry was found in the recipe search API."

    pantry_names = _get_pantry_names()
    result_data = []
    display_results = []
    for score, recipe in matches[:10]:
        try:
            parsed_ingredients = safe_parse_list(recipe["ingredients"])
            ingredients_list = ", ".join(format_ingredient_for_speech(ing) for ing in parsed_ingredients[:8])

            have, missing = _count_pantry_matches(parsed_ingredients, pantry_names)
            structured_recipe = (
                f"Title: {recipe['title']}, score: {score}, Ingredients: {ingredients_list}\n\n"
            )
            result_data.append(structured_recipe)
            display_results.append({
                "title": recipe["title"],
                "image_name": recipe.get("image_name"),
                "ingredient_count": len(parsed_ingredients),
                "have_count": have,
                "missing_count": missing,
            })
        except Exception as e:
            logger.debug(f"Skipping recipe: {e}")

    # Store for positional selection ("select the first one")
    _last_search_results = [r["title"] for _, r in matches[:10]]

    # Push search results to display
    event_system.publish(EventMessage(
        role="display",
        name="recipe_search",
        content={
            "title": f"Recipes: {query}",
            "query": query,
            "results": display_results,
        },
        process_output=False,
    ))

    # Inject display state so the LLM knows what the user is seeing
    titles_brief = ", ".join(r["title"] for r in display_results[:5])
    event_system.publish(EventMessage(
        "tool", "display_state",
        f"<display_state>The user is viewing recipe search results for '{query}'. "
        f"Showing: {titles_brief}. They can say 'the first one' or a recipe name to select.</display_state>",
        process_output=False,
    ))

    return (f"The following recipes were found by the recipe search API. Filter out all recipes "
            f"that are unrelated to the query '{query}', choose which best matches the query, "
            f"and ask the user to say 'select recipe' followed by the name of the recipe. "
            f"recipes: {result_data}")


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description=(
                                                     "Choose and load a specific recipe to start cooking. Use this when the user says "
                                                     "'let's make', 'I want to cook', 'select', or 'choose' a recipe. This activates "
                                                     "the recipe for step-by-step cooking."
                                             ),
                                             parameters=Parameters(type="object", required=['query'], properties={
                                                 'query': ParameterType(type="string",
                                                                        description="the name of the recipe to cook, e.g. 'apple pie', 'spaghetti', 'lasagna'")
                                             })
                                         )
                                         ),
    intents=[
        "lets make apple pie",
        "select a recipe for banana bread",
        "I want to make american pancakes",
        "lets cook spaghetti",
        "I want to cook dinner",
        "lets make something to eat",
        "cook some dinner",
        "select the pizza recipe",
        "choose the lasagna recipe",
        "make that recipe",
        "I want to bake cookies",
        "lets prepare a meal",
        "select pecan pralines",
        "select the first one",
        "select that one",
        "choose that recipe",
        "lets make pecan pralines",
        "make the cookies recipe",
        "the first one",
        "the second one",
        "the third one",
        "the last one",
        "number one",
        "number two",
        "that one",
    ],
    nlp_threshold=0.5,
    process_output=False,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def select_recipe(query: str) -> dict:
    """Select and activate a recipe for cooking."""
    try:
        matches = []
        for recipe in recipes:
            score = fuzz.partial_ratio(query.lower(), recipe['title'].lower())
            if score > 70:
                matches.append((score, recipe))

        if not matches:
            return {
                "status": "error",
                "message": "No matching recipe found. Ask the user to try a different name.",
            }

        best_match = max(matches, key=lambda x: x[0])[1]

        # Format ingredients for speech
        parsed_ingredients = safe_parse_list(best_match["ingredients"])
        ingredients_section = "\n".join(
            f"- {format_ingredient_for_speech(ing)}"
            for ing in parsed_ingredients
        )

        # Format directions for speech
        directions = best_match.get("directions", [])
        if isinstance(directions, str):
            directions = _split_instructions(directions)
        directions_section = "\n".join(
            f"Step {i + 1}: {format_ingredient_for_speech(step)}"
            for i, step in enumerate(directions)
        )

        # Auto-display recipe on connected screen (iPad)
        display_ingredients = safe_parse_list(best_match["ingredients"])
        display_directions = directions
        event_system.publish(EventMessage(
            role="display",
            name="recipe",
            content={
                "title": best_match["title"],
                "ingredients": display_ingredients,
                "directions": display_directions,
                "image_name": best_match.get("image_name"),
            },
            process_output=False
        ))

        # Inject recipe as tagged context so the LLM can answer follow-up
        # questions without reading the whole thing aloud. Published as a
        # non-process_output tool event, which ChatClient adds as a system message.
        recipe_context = (
            f'<active_recipe title="{best_match["title"]}">\n'
            f"<ingredients>\n{ingredients_section}\n</ingredients>\n"
            f"<directions>\n{directions_section}\n</directions>\n"
            f"</active_recipe>\n"
            f"The recipe above is displayed on screen. Answer questions about it naturally. "
            f"Do not read the full recipe unless the user explicitly asks to list ingredients or read steps."
        )
        event_system.publish(EventMessage(
            "tool", "recipe_context", recipe_context, process_output=False
        ))

        # Store full data for programmatic access (add_recipe_ingredients_to_list)
        global _last_selected_recipe
        _last_selected_recipe = {
            "title": best_match["title"],
            "ingredients": ingredients_section,
            "directions": directions_section,
        }

        # Return only brief confirmation — the recipe is on screen and
        # injected into context via <active_recipe> tags. Do NOT include
        # ingredients/directions here or the LLM will read them aloud.
        return {
            "status": "success",
            "title": best_match["title"],
            "message": f"I've put {best_match['title']} on the screen.",
        }

    except Exception as e:
        return {
            "status": "error",
            "message": "An unexpected error occurred while selecting the recipe.",
            "error": str(e),
        }


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Find recipes that use specific ingredients. Use when the user names specific ingredients they want to cook with.",
                                             parameters=Parameters(type="object", required=['query'], properties={
                                                 'query': ParameterType(type="string",
                                                                        description="comma-separated ingredient names, e.g. 'chicken, rice, garlic'")
                                             })
                                         )
                                         ),
    intents=[
        # Core: explicit ingredients mentioned by user
        "what can I make with chicken and rice",
        "what can I cook with chicken",
        "recipes with chicken and garlic",
        "find a recipe using eggs and cheese",
        "what can I make with potatoes",
        "recipes that use salmon",
        "what can I do with leftover chicken",
        "find me something with beef and peppers",
        "what uses up mushrooms",
        "I have chicken and pasta what can I make",
        # More explicit ingredient patterns
        "recipe ideas with lamb and rosemary",
        "what goes well with mushrooms and cream",
        "recipes using mince and potatoes",
        # AU/ZA: casual
        "chuck together something with chicken and rice",
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def find_recipe_by_ingredients(query: str) -> str:
    """Find recipes by specific ingredients the user mentions."""
    query_ingredients = [
        word.strip().lower()
        for word in re.split(r'[,\s]+', query)
        if word.strip() and len(word.strip()) > 2
    ]
    if not query_ingredients:
        return "No valid ingredients provided."

    # Normalize each ingredient for better matching
    normalized = []
    for ing in query_ingredients:
        norm, conf = normalize_ingredient(ing)
        normalized.append(norm if conf >= 0.85 else ing)

    matches = search_by_ingredients(normalized)

    if not matches:
        return ("DATA_RESPONSE:: The recipe API responded with no matching recipes found. Tell the user that no recipe "
                "was found, and cancel this line of inquiry. Make the response short, and to the point in this case.")

    # Push results to display
    pantry_names = _get_pantry_names()
    display_results = []
    for m in matches[:10]:
        ings = m.get("ingredients", [])
        have, missing = _count_pantry_matches(ings, pantry_names) if ings and pantry_names else (0, len(ings))
        display_results.append({
            "title": m["title"],
            "image_name": m.get("image_name"),
            "ingredient_count": len(ings),
            "have_count": have,
            "missing_count": missing,
        })
    global _last_search_results
    _last_search_results = [m["title"] for m in matches[:10]]
    search_method = matches[0].get("search_method", "fuzzy") if matches else ""
    event_system.publish(EventMessage(
        role="display", name="recipe_search",
        content={
            "title": f"Recipes with {', '.join(query_ingredients[:4])}",
            "query": query,
            "results": display_results,
            "search_method": search_method,
        },
        process_output=False,
    ))

    response = ("DATA_RESPONSE:: The recipe API responded with the following recipes, compare them to the original "
                "request, and then present the most likely matches present, use the similarity score internally only. "
                "Dont read the similarity score to the user, and then let the user choose a recipe to continue with. "
                ":\n")
    for match in matches[:5]:
        matched = ", ".join(match.get("matched_ingredients", []))
        response += f"- {match['title']} (matched: {matched})\n"
    return response


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Pick a random recipe the user can make based on pantry contents. Use when the user wants inspiration for what to cook.",
                                             parameters=Parameters(type="object", required=[], properties={
                                                 'category': ParameterType(type="string",
                                                                        description="Optional category filter, e.g. 'pasta', 'soups', 'baking'")
                                             })
                                         )
                                         ),
    intents=[
        # Core: surprise / inspiration — picks from pantry-weighted recipes
        "surprise me with a recipe",
        "surprise me with something to cook",
        "pick a recipe I can make",
        "pick something I can make from the pantry",
        "inspire me with a recipe",
        "give me a recipe idea",
        "chef's choice recipe",
        # Casual forms
        "just pick a recipe for me",
        "you choose a recipe",
        "dealer's choice",
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def surprise_me(category: str = None) -> dict:
    """Pick a random recipe weighted by pantry match."""
    import random
    pool = category_index.get(category, list(range(len(recipes)))) if category else list(range(len(recipes)))
    if not pool:
        return {"status": "error", "message": f"No recipes in category '{category}'."}

    pantry_set = set(_get_pantry_names())
    if pantry_set:
        weighted = []
        for idx in pool:
            core = set(recipes[idx].get("core_ingredients", []))
            match_pct = len(core & pantry_set) / len(core) * 100 if core else 0
            weight = max(1, match_pct ** 2)
            weighted.append((idx, weight))
        total_weight = sum(w for _, w in weighted)
        r = random.uniform(0, total_weight)
        cumulative = 0
        chosen_idx = weighted[0][0]
        for idx, w in weighted:
            cumulative += w
            if cumulative >= r:
                chosen_idx = idx
                break
    else:
        chosen_idx = random.choice(pool)

    recipe = recipes[chosen_idx]
    return select_recipe(recipe["title"])


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Pick a completely random recipe for exploration, regardless of pantry contents.",
                                             parameters=Parameters(type="object", required=[], properties={
                                                 'category': ParameterType(type="string",
                                                                        description="Optional category filter, e.g. 'pasta', 'chicken', 'baking'")
                                             })
                                         )
                                         ),
    intents=[
        # Core: truly random recipe (no pantry weighting)
        "random recipe",
        "random dinner idea",
        "random meal idea",
        "pick a random recipe",
        "show me a random recipe",
        "give me a random recipe",
        # Exploration phrasing
        "show me something new to cook",
        "show me something different to cook",
        "pick anything at random",
        "any random recipe",
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def random_recipe_tool(category: str = None) -> dict:
    """Pick a completely random recipe."""
    import random
    pool = category_index.get(category, list(range(len(recipes)))) if category else list(range(len(recipes)))
    if not pool:
        return {"status": "error", "message": "No recipes found."}
    chosen_idx = random.choice(pool)
    recipe = recipes[chosen_idx]
    return select_recipe(recipe["title"])


def add_user_recipe(title: str, ingredients: list[str], directions: list[str],
                    description: str = None, servings: int = 4, image_name: str = None) -> dict:
    """Add a user-created recipe to the system at runtime."""
    if not title or not ingredients:
        return {"status": "error", "message": "Title and at least one ingredient are required."}

    # Check for duplicate title
    title_lower = title.lower()
    for r in recipes:
        if r["title"].lower() == title_lower:
            return {"status": "exists", "message": f"A recipe called '{r['title']}' already exists."}

    cleaned = clean_ingredients(ingredients)
    core = [ingredient_map.get(c, c) for c in cleaned]
    category = _categorize_recipe(title)

    from datetime import datetime
    recipe = {
        "title": title,
        "ingredients": ingredients,
        "directions": directions,
        "cleaned_ingredients": cleaned,
        "core_ingredients": core,
        "image_name": image_name,
        "category": category,
        "meal_type": category,
        "cuisine": "other",
        "source": "user",
        "description": description,
        "servings": servings,
        "added": datetime.now().isoformat(timespec="seconds"),
    }

    recipe_idx = len(recipes)
    recipes.append(recipe)

    # Update indices
    ingredient_set.update(cleaned)
    for ing in core:
        for word in ing.split():
            if len(word) > 2:
                ingredient_index[word].add(recipe_idx)

    meal_key = f"meal:{category}"
    if meal_key not in category_index:
        category_index[meal_key] = []
    category_index[meal_key].append(recipe_idx)

    _save_user_recipes()

    # Publish event for Qdrant indexing + delta categorization
    event_system.publish(EventMessage(
        "tool", "recipe_added",
        {
            "title": title,
            "ingredients": ingredients,
            "directions": directions,
            "image_name": image_name,
            "index": recipe_idx,
        },
        process_output=False,
    ))

    logger.info(f"[Recipe] Added user recipe: '{title}' ({len(ingredients)} ingredients, {len(directions)} steps)")
    return {
        "status": "added",
        "title": title,
        "index": recipe_idx,
        "ingredient_count": len(cleaned),
        "message": f"Added '{title}' with {len(cleaned)} ingredients.",
    }


# Load recipes at initialization
load_recipes(data_file)

# Build ingredient parser vocabulary from loaded recipes
try:
    from glados.nlp.ingredient_parser import load_vocabulary_from_recipes
    load_vocabulary_from_recipes()
except Exception as e:
    logger.debug(f"Could not load ingredient parser vocabulary: {e}")


# -- UI action handler (direct SocketIO, no LLM/NLP) --------------------------

def _on_recipe_action(event):
    """Handle direct recipe UI actions from the display (no LLM round-trip)."""
    data = event.content if isinstance(event.content, dict) else {}
    action = data.get("action")

    if action == "search":
        query = data.get("query", "").strip()
        if query:
            search_recipes(query)
    elif action == "select":
        recipe_name = data.get("recipe_name", "").strip()
        if recipe_name:
            select_recipe(recipe_name)
    elif action == "search_from_pantry":
        expiring_items = data.get("expiring_items")
        try:
            from plugins.pantry.pantry_plugin import PantryPlugin
            pp = PantryPlugin()
            pp.suggest_meals_from_pantry(expiring_items=expiring_items)
        except Exception as e:
            logger.warning(f"[Recipe] Pantry search failed: {e}")
    elif action == "browse":
        # Show category grid
        cats = get_categories()
        event_system.publish(EventMessage(
            role="display", name="recipe_browse",
            content={"title": "Browse Recipes", "categories": cats, "mode": "categories"},
            process_output=False,
        ))
    elif action == "browse_category":
        category = data.get("category", "")
        offset = data.get("offset", 0)
        limit = data.get("limit", 20)
        items = get_recipes_by_category(category, offset, limit)
        # Look up total from prefixed or unprefixed key
        if category.startswith(("meal:", "cuisine:")):
            total = len(category_index.get(category, []))
        else:
            total = len(category_index.get(f"meal:{category}", []) or category_index.get(f"cuisine:{category}", []))
        cat_label = category.replace("meal:", "").replace("cuisine:", "").replace("_", " ").title()
        event_system.publish(EventMessage(
            role="display", name="recipe_browse",
            content={
                "title": f"{cat_label} Recipes",
                "category": category,
                "items": items,
                "offset": offset,
                "total": total,
                "mode": "list",
            },
            process_output=False,
        ))
    elif action == "random_recipe":
        import random
        category = data.get("category")
        pantry_aware = data.get("pantry_aware", False)
        pool = category_index.get(category, list(range(len(recipes)))) if category else list(range(len(recipes)))
        if not pool:
            return
        if pantry_aware:
            # Weighted random by pantry match
            pantry_set = set(_get_pantry_names())
            if pantry_set:
                weighted = []
                for idx in pool:
                    core = set(recipes[idx].get("core_ingredients", []))
                    match_pct = len(core & pantry_set) / len(core) * 100 if core else 0
                    weight = max(1, match_pct ** 2)  # square to heavily favor high matches
                    weighted.append((idx, weight))
                total_weight = sum(w for _, w in weighted)
                r = random.uniform(0, total_weight)
                cumulative = 0
                chosen_idx = weighted[0][0]
                for idx, w in weighted:
                    cumulative += w
                    if cumulative >= r:
                        chosen_idx = idx
                        break
            else:
                chosen_idx = random.choice(pool)
        else:
            chosen_idx = random.choice(pool)
        recipe = recipes[chosen_idx]
        select_recipe(recipe["title"])
    elif action == "add_recipe":
        title = data.get("title", "").strip()
        ingredients = [i.strip() for i in data.get("ingredients", []) if i.strip()]
        directions = [d.strip() for d in data.get("directions", []) if d.strip()]
        description = data.get("description", "").strip() or None
        servings = data.get("servings", 4)
        result = add_user_recipe(title, ingredients, directions, description, servings)
        if result.get("status") == "added":
            # Show the newly added recipe
            select_recipe(title)
    elif action == "show_add_form":
        event_system.publish(EventMessage(
            role="display", name="recipe_add",
            content={"title": "Add Recipe"},
            process_output=False,
        ))


# Register the UI action with the plugin system
plugin_manager.register_ui_action("recipe_action", _on_recipe_action)

# Register display views
plugin_manager.register_view("recipe", "plugins/recipes/views/recipe.js", css_path="plugins/recipes/views/recipe.css", dashboard_card=True)
plugin_manager.register_view("recipe_search", "plugins/recipes/views/recipe.js", css_path="plugins/recipes/views/recipe.css")
plugin_manager.register_view("recipe_browse", "plugins/recipes/views/browse.js", css_path="plugins/recipes/views/recipe.css")
plugin_manager.register_view("recipe_add", "plugins/recipes/views/add_recipe.js", css_path="plugins/recipes/views/recipe.css")

# Subscribe to the event so the handler gets called
from glados.system.event_system import EventHook
event_system.subscribe(
    "ui.recipe_action",
    EventHook("recipe_ui_handler", callback=_on_recipe_action, priority=5)
)


# -- NLP mode handlers ---------------------------------------------------------
def _recipe_query_extract(text: str) -> dict:
    """Extract recipe query from natural language."""
    for pattern in [
        r"^(?:please\s+)?(?:find|search|look up|get)\s+(?:me\s+)?(?:a\s+)?(?:recipes?\s+)?(?:for\s+)?",
        r"^(?:what\s+recipes?\s+(?:for|do you know)\s+)",
        r"^(?:I\s+need\s+(?:a\s+)?recipe\s+(?:for\s+)?)",
        r"^(?:do\s+you\s+have\s+(?:a\s+)?recipe\s+(?:for\s+)?)",
        r"^(?:recipe\s+(?:search\s+)?(?:for\s+)?)",
    ]:
        stripped = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
        if stripped and stripped != text:
            return {"query": stripped}
    return {"query": text}


def _search_recipes_nlp_response(result) -> str:
    if isinstance(result, str):
        if "No recipes found" in result:
            return "I couldn't find any matching recipes."
        titles = re.findall(r"Title:\s*([^,]+)", result)
        if titles:
            top = titles[:5]
            listing = ", ".join(top[:-1]) + f", and {top[-1]}" if len(top) > 1 else top[0]
            return (f"I found {len(titles)} recipes. The top matches are: {listing}. "
                    f"Say the first one, the second one, or the recipe name to select.")
        return "I found some recipes. Which one would you like?"
    if isinstance(result, dict) and result.get("status") == "error":
        return result.get("message", "Recipe search failed.")
    return "I found some recipes. Which one would you like?"


_ORDINALS = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "sixth": 5, "seventh": 6, "eighth": 7, "ninth": 8, "tenth": 9,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
    "last": -1,
}


def _select_recipe_extract(text: str) -> dict:
    """Extract recipe name for selection. Supports positional references."""
    text_lower = text.lower().strip()

    # Check for positional selection: "the first one", "number 3", "select 2"
    for word, idx in _ORDINALS.items():
        if word in text_lower:
            if _last_search_results:
                actual_idx = idx if idx >= 0 else len(_last_search_results) + idx
                if 0 <= actual_idx < len(_last_search_results):
                    return {"query": _last_search_results[actual_idx]}

    # "number N" / "select N" / just a digit
    num_match = re.search(r"(?:number|select|recipe)\s*(\d+)", text_lower)
    if not num_match:
        num_match = re.match(r"^\s*(\d+)\s*$", text_lower)
    if num_match and _last_search_results:
        idx = int(num_match.group(1)) - 1  # 1-indexed
        if 0 <= idx < len(_last_search_results):
            return {"query": _last_search_results[idx]}

    # Standard name extraction
    query = re.sub(
        r"^(?:please\s+)?(?:let'?s\s+(?:make|cook|prepare|bake)"
        r"|select\s+(?:a\s+)?(?:the\s+)?(?:recipe\s+)?(?:for\s+)?"
        r"|I\s+want\s+to\s+(?:make|cook|bake|prepare)"
        r"|(?:cook|make|bake|prepare)\s+(?:some\s+)?(?:the\s+)?(?:recipe\s+)?(?:for\s+)?)\s*",
        "", text, flags=re.IGNORECASE
    ).strip()
    return {"query": query or text}


def _select_recipe_nlp_response(result: dict) -> str:
    if result.get("status") == "error":
        return result.get("message", "Couldn't find that recipe.")
    title = result.get("title", "the recipe")
    return (f"Selected {title}. It has been displayed on screen. "
            f"You can ask me to list ingredients, read the steps, or go step by step.")


def _ingredient_search_extract(text: str) -> dict:
    """Extract ingredient names from 'what can I make with X and Y' queries."""
    # Strip common preambles
    cleaned = re.sub(
        r"^(?:what\s+(?:can\s+I|could\s+I|do\s+you)\s+(?:make|cook|do|bake)\s+(?:with|using|from)\s+)"
        r"|^(?:(?:find|search|get)\s+(?:me\s+)?(?:a\s+)?(?:recipes?\s+)?(?:with|using|that\s+use)\s+)"
        r"|^(?:recipes?\s+(?:with|using|that\s+use)\s+)"
        r"|^(?:I\s+have\s+)",
        "", text, flags=re.IGNORECASE
    ).strip()
    # Strip trailing filler
    cleaned = re.sub(r"\s+(?:what\s+can\s+I\s+(?:make|cook)|recipes?)?\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    # Split on "and", commas, whitespace
    parts = re.split(r"\s+and\s+|,\s*", cleaned)
    ingredients = [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]
    return {"query": ", ".join(ingredients)} if ingredients else {"query": text}


def _ingredient_search_nlp_response(result) -> str:
    if isinstance(result, str):
        if "no matching recipes" in result.lower():
            return "I couldn't find any recipes with those ingredients."
    return "I found some recipes and put them on the screen. Say the first one, or the recipe name, to select."


# Register NLP handlers for recipe tools
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry

_nlp_registry = NLPHandlerRegistry()
_nlp_registry.register(NLPHandler(
    tool_name="search_recipes",
    extract_fn=_recipe_query_extract,
    response_fn=_search_recipes_nlp_response,
))
_nlp_registry.register(NLPHandler(
    tool_name="select_recipe",
    extract_fn=_select_recipe_extract,
    response_fn=_select_recipe_nlp_response,
))
_nlp_registry.register(NLPHandler(
    tool_name="find_recipe_by_ingredients",
    extract_fn=_ingredient_search_extract,
    response_fn=_ingredient_search_nlp_response,
))


def _surprise_extract(text: str) -> dict:
    """Extract optional category from surprise/random requests."""
    for cat in RECIPE_CATEGORIES:
        if cat in text.lower():
            return {"category": cat}
    return {}


def _surprise_nlp_response(result) -> str:
    if isinstance(result, dict) and result.get("status") == "error":
        return result.get("message", "No recipes found.")
    title = result.get("title", "a recipe") if isinstance(result, dict) else "a recipe"
    return f"How about {title}? I've put it on the screen."


_nlp_registry.register(NLPHandler(
    tool_name="surprise_me",
    extract_fn=_surprise_extract,
    response_fn=_surprise_nlp_response,
))
_nlp_registry.register(NLPHandler(
    tool_name="random_recipe_tool",
    extract_fn=_surprise_extract,
    response_fn=_surprise_nlp_response,
))
