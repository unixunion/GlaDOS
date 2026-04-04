"""Meal planning optimizer — pure functions for ingredient optimization.

Handles:
- Building reverse ingredient→recipe index
- Finding missing ingredients for planned meals
- Greedy set-cover for staple ingredient suggestions
- Quantity aggregation and scaling
- Meal suggestions from favorites + pantry
"""
import re
from collections import defaultdict

from loguru import logger
from rapidfuzz import fuzz


def _get_recipes():
    """Import recipe data lazily."""
    from plugins.recipes.recipe_api import recipes
    return recipes


def build_ingredient_recipe_index(recipes: list = None) -> dict[str, set[int]]:
    """Build reverse index: core ingredient → set of recipe indices."""
    if recipes is None:
        recipes = _get_recipes()
    index = defaultdict(set)
    for idx, recipe in enumerate(recipes):
        for ing in recipe.get("core_ingredients", recipe.get("cleaned_ingredients", [])):
            index[ing].add(idx)
    return dict(index)


def ingredient_frequency(recipes: list = None) -> dict[str, int]:
    """Get frequency of each core ingredient across all recipes."""
    index = build_ingredient_recipe_index(recipes)
    return {ing: len(idxs) for ing, idxs in index.items()}


def count_missing_ingredients(planned_titles: list[str], pantry_items: list[str]) -> int:
    """Count total missing ingredients for planned recipes."""
    recipes = _get_recipes()
    pantry_set = set(p.lower() for p in pantry_items)
    missing = set()
    for title in planned_titles:
        recipe = _find_recipe(title, recipes)
        if recipe:
            for ing in recipe.get("core_ingredients", []):
                if ing not in pantry_set:
                    missing.add(ing)
    return len(missing)


def _find_recipe(title: str, recipes: list) -> dict | None:
    """Find recipe by fuzzy title match."""
    best, best_score = None, 0
    title_lower = title.lower()
    for r in recipes:
        score = fuzz.ratio(title_lower, r["title"].lower())
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 70 else None


def _parse_quantity_number(quantity_str: str) -> float | None:
    """Extract numeric value from a quantity string like '2 cups', '1/2', '1 1/2'."""
    if not quantity_str:
        return None
    # Unicode fractions
    frac_map = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 0.333, "⅔": 0.667, "⅛": 0.125}
    for char, val in frac_map.items():
        if char in quantity_str:
            # Handle mixed: "1 ½" → 1.5
            prefix = quantity_str.split(char)[0].strip()
            base = float(prefix) if prefix and prefix.replace(".", "").isdigit() else 0
            return base + val

    # Standard fractions: "1/2", "3/4"
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)", quantity_str)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)", quantity_str)
    if m:
        return int(m.group(1)) / int(m.group(2))

    # Simple number
    m = re.match(r"^([\d.]+)", quantity_str)
    if m:
        return float(m.group(1))

    return None


def _round_to_quarter(n: float) -> str:
    """Round to nearest quarter and format nicely."""
    rounded = round(n * 4) / 4
    if rounded == int(rounded):
        return str(int(rounded))
    whole = int(rounded)
    frac = rounded - whole
    frac_map = {0.25: "¼", 0.5: "½", 0.75: "¾"}
    frac_str = frac_map.get(frac, f"{frac:.2f}")
    return f"{whole} {frac_str}" if whole else frac_str


def scale_quantity(quantity_str: str, scale_factor: float) -> str:
    """Scale a quantity string by the given factor."""
    if not quantity_str or scale_factor == 1.0:
        return quantity_str
    num = _parse_quantity_number(quantity_str)
    if num is None:
        return quantity_str  # "to taste", "pinch", etc.
    # Extract the unit part (everything after the number)
    unit = re.sub(r"^[\d\s½¼¾⅓⅔⅛/.]+", "", quantity_str).strip()
    scaled = num * scale_factor
    return f"{_round_to_quarter(scaled)} {unit}".strip()


def generate_optimized_list(
    planned_titles: list[str],
    pantry_items: set[str],
    household: dict,
    staple_budget: int = 5,
) -> dict:
    """Generate an optimized shopping list from planned meals.

    Returns:
        {
            "required_items": [{"ingredient": str, "quantity": str|None}],
            "skipped": int,
            "staple_suggestions": [{"ingredient": str, "unlocks": int, "example_recipes": [str]}],
        }
    """
    recipes = _get_recipes()
    pantry_lower = {p.lower() for p in pantry_items}

    # Calculate scale factor
    adults = household.get("adults", 2)
    children = household.get("children", 0)
    child_factor = household.get("child_portion_factor", 0.5)
    default_servings = 4
    scale_factor = (adults + children * child_factor) / default_servings

    # Resolve planned recipes and collect ingredients
    ingredient_quantities = defaultdict(list)  # ingredient → list of quantity strings
    all_core = set()
    for title in planned_titles:
        recipe = _find_recipe(title, recipes)
        if not recipe:
            continue

        core_ings = recipe.get("core_ingredients", [])
        raw_ings = recipe.get("ingredients", [])
        all_core.update(core_ings)

        # Parse quantities from raw ingredients
        try:
            from glados.nlp.ingredient_parser import parse_ingredient_list
            parsed = parse_ingredient_list(raw_ings if isinstance(raw_ings, list) else [raw_ings])
            for p in parsed:
                item = p["item"].lower()
                # Map to core ingredient name
                from plugins.recipes.recipe_api import ingredient_map
                core = ingredient_map.get(item, item)
                if p.get("quantity"):
                    ingredient_quantities[core].append(p["quantity"])
        except ImportError:
            pass

    # Split into required (not in pantry) and skipped (in pantry)
    required = []
    skipped = 0
    for ing in sorted(all_core):
        if ing in pantry_lower:
            skipped += 1
            continue
        # Aggregate quantities
        quantities = ingredient_quantities.get(ing, [])
        if quantities and scale_factor != 1.0:
            quantities = [scale_quantity(q, scale_factor) for q in quantities]
        # Combine multiple quantities into one string
        quantity = ", ".join(quantities) if quantities else None
        required.append({"ingredient": ing, "quantity": quantity})

    # Staple gap-filler suggestions
    staple_suggestions = _score_staple_candidates(
        owned=pantry_lower | {r["ingredient"] for r in required},
        recipes=recipes,
        budget=staple_budget,
    )

    return {
        "required_items": required,
        "skipped": skipped,
        "staple_suggestions": staple_suggestions,
    }


def _score_staple_candidates(
    owned: set[str],
    recipes: list,
    budget: int = 5,
) -> list[dict]:
    """Score candidate staple ingredients by how many new recipes they unlock.

    Uses greedy set-cover: pick the candidate that unlocks the most recipes,
    add it to owned, repeat.
    """
    # Build recipe → core_ingredients set for fast checking
    recipe_sets = []
    for r in recipes:
        core = set(r.get("core_ingredients", r.get("cleaned_ingredients", [])))
        recipe_sets.append(core)

    # Pre-filter: only consider ingredients appearing in 20+ recipes
    index = build_ingredient_recipe_index(recipes)
    candidates = [ing for ing, idxs in index.items() if len(idxs) >= 20 and ing not in owned]

    suggestions = []
    current_owned = set(owned)

    for _ in range(budget):
        best_ing, best_score, best_examples = None, 0, []

        for cand in candidates:
            if cand in current_owned:
                continue
            test_owned = current_owned | {cand}
            unlocked = []
            for idx in index[cand]:
                if recipe_sets[idx].issubset(test_owned):
                    unlocked.append(idx)
            if len(unlocked) > best_score:
                best_ing = cand
                best_score = len(unlocked)
                best_examples = [recipes[i]["title"] for i in unlocked[:3]]

        if not best_ing or best_score == 0:
            break

        suggestions.append({
            "ingredient": best_ing,
            "unlocks": best_score,
            "example_recipes": best_examples,
        })
        current_owned.add(best_ing)

    return suggestions


def suggest_meals(
    favorites: list[str],
    pantry_items: set[str],
    count: int = 5,
) -> list[dict]:
    """Suggest diverse meals prioritizing favorites with high pantry match."""
    recipes = _get_recipes()
    pantry_lower = {p.lower() for p in pantry_items}
    fav_set = {f.lower() for f in favorites}

    scored = []
    for recipe in recipes:
        core = set(recipe.get("core_ingredients", []))
        if not core:
            continue
        have = core & pantry_lower
        match_pct = len(have) / len(core) * 100 if core else 0
        is_fav = recipe["title"].lower() in fav_set
        # Score: favorites get 50 point bonus, rest is pantry match %
        score = match_pct + (50 if is_fav else 0)
        scored.append({
            "title": recipe["title"],
            "image_name": recipe.get("image_name"),
            "pantry_match_pct": round(match_pct),
            "missing_count": len(core - pantry_lower),
            "is_favorite": is_fav,
            "score": score,
            "core_ingredients": core,
        })

    # Sort by score, then diversify: don't pick recipes with highly overlapping ingredients
    scored.sort(key=lambda x: x["score"], reverse=True)

    selected = []
    used_ingredients = set()
    for candidate in scored:
        if len(selected) >= count:
            break
        # Diversity check: at least 30% of ingredients should be different from already selected
        core = candidate["core_ingredients"]
        overlap = len(core & used_ingredients) / len(core) if core else 1
        if overlap > 0.7 and len(selected) > 0:
            continue
        used_ingredients.update(core)
        # Remove internal fields
        selected.append({k: v for k, v in candidate.items() if k not in ("score", "core_ingredients")})

    return selected
