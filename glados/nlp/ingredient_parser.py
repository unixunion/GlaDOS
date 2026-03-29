"""Ingredient parser — extracts item name, quantity, and modifiers from recipe ingredient strings.

Uses a vocabulary of known ingredients built from the recipe dataset for fuzzy matching.
Can be used programmatically from any plugin.

Usage:
    from glados.nlp.ingredient_parser import parse_ingredient
    result = parse_ingredient("2 tablespoons fresh lemon juice")
    # → {"item": "lemon juice", "quantity": "2 tablespoons", "modifier": "fresh"}
"""

import re
from typing import Optional

from loguru import logger
from rapidfuzz import fuzz

# Quantity units (for stripping from item names)
_UNITS = (
    "tablespoons?|tbsps?|teaspoons?|tsps?|cups?|ounces?|oz|pounds?|lbs?|lb|"
    "pints?|quarts?|gallons?|gal|liters?|milliliters?|ml|"
    "cloves?|stalks?|sprigs?|slices?|pieces?|heads?|bunche?s?|cans?|"
    "packages?|pkgs?|pinche?s?|dashe?s?|sticks?|bags?|bottles?|jars?|"
    "handfuls?|drops?|sheets?"
)

# Modifiers that describe preparation, not the item itself
_MODIFIERS = {
    "fresh", "freshly", "finely", "roughly", "thinly", "thickly",
    "chopped", "diced", "minced", "sliced", "grated", "shredded",
    "crushed", "ground", "crumbled", "melted", "softened", "toasted",
    "roasted", "dried", "frozen", "canned", "cooked", "uncooked", "raw",
    "peeled", "seeded", "pitted", "trimmed", "halved", "quartered",
    "divided", "packed", "sifted", "beaten", "room-temperature",
    "large", "medium", "small", "extra-large", "extra",
    "boneless", "skinless", "skin-on", "bone-in",
    "whole", "plus", "more", "about", "approximately", "optional",
}

# Non-food items to filter out
_NON_FOOD = re.compile(
    r"\b(thermometer|equipment|pan|skillet|bowl|sheet|rack|foil|parchment|"
    r"towel|twine|cheesecloth|sieve|strainer|grater|peeler|brush|tongs|"
    r"baking\s+sheet|cutting\s+board|food\s+processor|blender|mixer)\b",
    re.IGNORECASE,
)

# Regex for quantity at the start of an ingredient line
_QTY_PATTERN = re.compile(
    r"^([\d½¼¾⅓⅔⅛⅜⅝⅞][\d\s½¼¾⅓⅔⅛⅜⅝⅞/.\-–]*)\s*"
    rf"({_UNITS})?\s*"
    r"(?:of\s+)?",
    re.IGNORECASE,
)

# Known ingredient vocabulary (populated from recipe dataset)
_known_items: set = set()


def load_vocabulary_from_recipes():
    """Build the ingredient vocabulary from the loaded recipe dataset."""
    global _known_items
    try:
        from plugins.recipes.recipe_api import recipes, safe_parse_list
        items = set()
        for r in recipes:
            raw_ings = safe_parse_list(r.get("ingredients", []))
            for ing in raw_ings:
                # Extract just the core item name
                parsed = _parse_raw(ing)
                if parsed["item"] and len(parsed["item"]) > 2:
                    items.add(parsed["item"].lower())
        _known_items = items
        logger.info(f"[IngredientParser] Loaded {len(_known_items)} unique ingredient names")
    except Exception as e:
        logger.warning(f"[IngredientParser] Could not load vocabulary: {e}")


def _parse_raw(text: str) -> dict:
    """Parse a raw ingredient string into components without vocabulary matching."""
    text = text.strip().lstrip("- •").strip()
    if not text:
        return {"item": "", "quantity": None, "modifier": None}

    # Strip parenthetical notes: (optional), (about 2 cups), etc.
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text).strip()
    # Strip trailing comma-separated notes: ", plus more for serving"
    text = re.sub(r",\s+plus\s+more.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r",\s+divided\s*$", "", text, flags=re.IGNORECASE).strip()

    # Extract quantity
    quantity = None
    m = _QTY_PATTERN.match(text)
    if m:
        qty_num = m.group(1).strip()
        qty_unit = m.group(2) or ""
        quantity = f"{qty_num} {qty_unit}".strip()
        text = text[m.end():].strip()

    # Extract modifiers
    words = text.split()
    modifiers = []
    item_words = []
    for word in words:
        clean_word = word.lower().rstrip(",;.")
        if clean_word in _MODIFIERS:
            modifiers.append(clean_word)
        else:
            item_words.append(word)

    modifier = " ".join(modifiers) if modifiers else None
    item = " ".join(item_words).strip().rstrip(",;.").lower()

    return {"item": item, "quantity": quantity, "modifier": modifier}


def parse_ingredient(text: str) -> dict:
    """Parse a recipe ingredient string into structured components.

    Returns:
        dict with keys:
            - item: the core ingredient name (e.g. "lemon juice")
            - quantity: the amount (e.g. "2 tablespoons") or None
            - modifier: preparation notes (e.g. "fresh, finely chopped") or None
            - is_food: False if this looks like equipment, not an ingredient
    """
    result = _parse_raw(text)
    result["is_food"] = not bool(_NON_FOOD.search(text))

    # If we have a vocabulary, try to match against known items for normalization
    if _known_items and result["item"]:
        item_lower = result["item"]
        # Exact match first
        if item_lower in _known_items:
            return result
        # Fuzzy match against known items (only check items starting with same letter for speed)
        first_char = item_lower[0] if item_lower else ""
        candidates = [k for k in _known_items if k and k[0] == first_char]
        best_match, best_score = None, 0
        for candidate in candidates:
            score = fuzz.ratio(item_lower, candidate)
            if score > best_score:
                best_match, best_score = candidate, score
        if best_match and best_score >= 85:
            result["item"] = best_match

    return result


def parse_ingredient_list(ingredients: list[str]) -> list[dict]:
    """Parse a list of ingredient strings, filtering out non-food items."""
    results = []
    for ing in ingredients:
        parsed = parse_ingredient(ing)
        if parsed["is_food"] and parsed["item"] and len(parsed["item"]) > 1:
            results.append(parsed)
    return results
