"""Shopping List + Pantry Management plugin for GlaDOS.

Provides durable shopping list and pantry inventory tracking with voice commands,
NLP fast-path handlers, and interactive display views.
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, date
from typing import Optional

from loguru import logger
from rapidfuzz import fuzz

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage, EventHook

from plugins.pantry.shopping_context import ShoppingContextMixin
from plugins.pantry.recipe_integration import RecipeIntegrationMixin
from plugins.pantry.nlp_handlers import (
    _add_to_list_nlp_extract, _add_to_list_nlp_response,
    _remove_from_list_nlp_extract, _remove_from_list_nlp_response,
    _show_list_nlp_response,
    _complete_shopping_nlp_extract, _complete_shopping_nlp_response,
    _store_item_nlp_extract, _store_item_nlp_response,
    _set_expiry_nlp_extract, _set_expiry_nlp_response,
    _find_item_nlp_extract, _find_item_nlp_response,
    _check_expiring_nlp_extract, _check_expiring_nlp_response,
    _show_pantry_nlp_extract, _show_pantry_nlp_response,
    _manage_locations_nlp_extract, _manage_locations_nlp_response,
)


# ---------------------------------------------------------------------------
# Category auto-assignment
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "dairy": ["milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "eggs", "egg"],
    "dry_goods": [
        "rice", "pasta", "flour", "cornflour", "sugar", "cereal", "oats", "lentils",
        "couscous", "noodles", "spaghetti", "tagliatelle", "lasagne tiles", "lasagne sheets",
        "baking powder", "bicarbonate", "canned", "tinned",
    ],
    "spices": [
        "paprika", "cayenne", "thyme", "oregano", "cumin", "cinnamon", "turmeric",
        "chilli powder", "chili powder", "nutmeg", "coriander", "basil", "rosemary",
        "bay leaves", "cloves", "cardamom", "fennel seed",
        "black pepper", "white pepper", "ground pepper", "peppercorn"
    ],
    "meat": [
        "chicken", "beef", "pork", "bacon", "sausage", "mince", "steak", "lamb",
        "turkey", "ham", "salami", "prosciutto", "duck",
    ],
    "seafood": ["fish", "salmon", "tuna", "prawns", "shrimp", "cod", "haddock"],
    "produce": [
        "apple", "banana", "lettuce", "tomato", "onion", "potato", "carrot",
        "bell pepper", "cucumber", "spinach", "broccoli", "avocado", "lemon", "lime",
        "garlic", "ginger", "mushroom", "celery", "corn on the cob", "peas", "beans",
    ],
    "frozen": ["ice cream", "frozen peas", "fish fingers", "pizza"],
    "bakery": ["bread", "rolls", "croissant", "bagel", "muffin", "wrap", "tortilla", "pitta"],
    "beverages": ["juice", "water", "soda", "coffee", "tea", "beer", "wine"],
    "condiments": ["ketchup", "mustard", "mayo", "mayonnaise", "sauce", "oil", "vinegar", "salt"],
    "snacks": ["crisps", "chips", "nuts", "chocolate", "biscuits", "cookies", "popcorn", "seeds"],
}


def _categorize_item(name: str) -> str:
    """Auto-assign a shopping category based on item name."""
    name_lower = name.lower()
    # Exact substring match first (avoids fuzzy false positives like "salami" matching "salt")
    for category, keywords in CATEGORY_MAP.items():
        for keyword in keywords:
            if keyword in name_lower:
                return category
    # Fuzzy fallback for misspellings (ratio not partial_ratio — measures spelling similarity)
    for category, keywords in CATEGORY_MAP.items():
        for keyword in keywords:
            if fuzz.ratio(keyword, name_lower) >= 85:
                return category
    return "other"


# Ready meal keywords — items matching these are classified as complete dishes
READY_MEAL_KEYWORDS = [
    "lasagna", "lasagne", "pizza", "pie", "soup", "stew", "curry", "casserole",
    "quiche", "pasta bake", "mac and cheese", "shepherd's pie", "cottage pie",
    "ready meal", "tv dinner", "microwave meal", "frozen dinner", "frozen meal",
    "leftover", "leftovers", "cooked", "prepared", "meal prep",
    "burrito", "filled wrap", "sandwich", "salad", "risotto", "paella",
    "chili", "chilli", "bolognese", "moussaka", "enchilada", "frittata",
    "tikka", "korma", "biryani", "stir fry", "stir-fry", "fried rice",
    "roast", "gratin", "ratatouille", "goulash", "tagine",
    "dumplings", "gyoza", "samosa", "empanada", "spring rolls",
    "fish cake", "fishcake", "fish fingers", "nuggets", "schnitzel",
    "meatballs", "meatloaf", "pot pie", "calzone", "focaccia",
]


# Words that override a ready_meal match — these are ingredients, not meals
_INGREDIENT_OVERRIDES = [
    "tiles", "sheets", "sauce", "paste", "powder", "seasoning", "mix",
    "base", "stock", "broth", "dried", "canned", "tinned", "raw",
]


def _classify_item_type(name: str) -> str:
    """Classify a pantry item as 'ready_meal' or 'ingredient'.

    Uses substring matching only (no fuzzy) to avoid false positives
    like 'rice' matching 'risotto'. Multi-word keywords use token overlap.
    """
    name_lower = name.lower()
    # Check overrides first — if the name contains an ingredient indicator, it's not a meal
    if any(ov in name_lower for ov in _INGREDIENT_OVERRIDES):
        return "ingredient"
    for keyword in READY_MEAL_KEYWORDS:
        if keyword in name_lower:
            return "ready_meal"
        # For multi-word items, check if all keyword words appear in the name
        kw_words = keyword.split()
        if len(kw_words) > 1 and all(w in name_lower for w in kw_words):
            return "ready_meal"
    return "ingredient"


# ---------------------------------------------------------------------------
# Shelf life estimation defaults (days) — keyed by category → location type
# ---------------------------------------------------------------------------

DEFAULT_SHELF_LIFE = {
    "dairy":      {"fridge": 10,  "freezer": 90,  "room_temp": 1},
    "produce":    {"fridge": 7,   "freezer": 180, "room_temp": 3},
    "meat":       {"fridge": 3,   "freezer": 180, "room_temp": 0},
    "seafood":    {"fridge": 2,   "freezer": 180, "room_temp": 0},
    "frozen":     {"fridge": 3,   "freezer": 180, "room_temp": 1},
    "bakery":     {"fridge": 7,   "freezer": 90,  "room_temp": 4},
    "dry_goods":  {"fridge": 365, "freezer": 365, "room_temp": 365},
    "beverages":  {"fridge": 30,  "freezer": 180, "room_temp": 30},
    "condiments": {"fridge": 180, "freezer": 365, "room_temp": 90},
    "snacks":     {"fridge": 30,  "freezer": 90,  "room_temp": 30},
    "spices":     {"fridge": 365, "freezer": 365, "room_temp": 365},
    "other":      {"fridge": 7,   "freezer": 90,  "room_temp": 14},
    "ready_meal": {"fridge": 3,   "freezer": 90,  "room_temp": 1},
}

# Suggested storage type per category — used for "put away" flow
CATEGORY_LOCATION_SUGGESTION = {
    "dairy": "fridge",
    "produce": "fridge",
    "meat": "fridge",
    "seafood": "fridge",
    "frozen": "freezer",
    "bakery": "room_temp",
    "dry_goods": "room_temp",
    "beverages": "fridge",
    "condiments": "fridge",
    "spices": "room_temp",
    "snacks": "room_temp",
    "ready_meal": "fridge",
    "other": "fridge",
}

# Keywords in location names used to infer storage type
LOCATION_TYPE_HINTS = {
    "fridge": "fridge",
    "refrigerator": "fridge",
    "freezer": "freezer",
    "dry": "room_temp",
    "cupboard": "room_temp",
    "spice": "room_temp",
    "pantry": "room_temp",
    "counter": "room_temp",
    "shelf": "room_temp",
    "cabinet": "room_temp",
    "garage": "room_temp",
}


def _infer_location_type(loc_id: str, loc_name: str) -> str:
    """Infer storage type from location id/name. Returns fridge, freezer, or room_temp."""
    combined = f"{loc_id} {loc_name}".lower()
    for keyword, loc_type in LOCATION_TYPE_HINTS.items():
        if keyword in combined:
            return loc_type
    return "room_temp"


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

def _parse_expiry_date(text: str) -> Optional[str]:
    """Parse a human expiry date expression into ISO date string.

    Handles: "the 24th", "March 24th", "26th June", "2026-06-26", "tomorrow"
    Returns ISO date string (YYYY-MM-DD) or None.
    """
    text = text.strip().lower()

    if text == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    if text == "today":
        return date.today().isoformat()

    # Try ISO format first
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass

    # Try common date formats
    for fmt in ("%d %B %Y", "%d %B", "%B %d", "%d/%m/%Y", "%d/%m", "%dth %B", "%dst %B", "%dnd %B", "%drd %B"):
        cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text)
        try:
            parsed = datetime.strptime(cleaned.strip(), fmt)
            if parsed.year == 1900:  # no year provided
                parsed = parsed.replace(year=date.today().year)
                if parsed.date() < date.today():
                    parsed = parsed.replace(year=date.today().year + 1)
            return parsed.date().isoformat()
        except ValueError:
            continue

    # "the 24th" — just a day number, assume current or next month
    m = re.match(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?$", text)
    if m:
        day = int(m.group(1))
        today = date.today()
        try:
            target = today.replace(day=day)
            if target < today:
                # Next month
                if today.month == 12:
                    target = target.replace(year=today.year + 1, month=1)
                else:
                    target = target.replace(month=today.month + 1)
            return target.isoformat()
        except ValueError:
            pass

    return None


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class PantryPlugin(ShoppingContextMixin, RecipeIntegrationMixin, RunnableMCPPlugin):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(PantryPlugin, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        super().__init__()

        self._data_dir = os.environ.get("PANTRY_DATA_DIR", os.path.join("plugin_data", "pantry"))
        os.makedirs(self._data_dir, exist_ok=True)

        self._shopping_list = self._load_json("shopping_list.json", {"items": [], "recurring_rules": []})
        self._pantry = self._load_json("pantry.json", {
            "locations": [
                {"id": "fridge", "name": "Fridge", "type": "fridge"},
                {"id": "freezer-1", "name": "Freezer Drawer 1", "type": "freezer"},
                {"id": "freezer-2", "name": "Freezer Drawer 2", "type": "freezer"},
                {"id": "freezer-3", "name": "Freezer Drawer 3", "type": "freezer"},
                {"id": "dry-goods", "name": "Dry Goods Cupboard", "type": "room_temp"},
            ],
            "items": [],
            "shelf_life_config": {},
        })
        self._migrate_pantry_data()

        self._last_recurring_check = None
        self._expiry_warned_today = False
        self._startup_time = datetime.now()
        self._shopping_mode = None  # None, "planning", or "post_shopping"
        self._catalog_mode = None    # None or {"location_id": str, "location_name": str, "mentioned": set(), "added": int, "updated": int, "removed": int}
        self._last_added_item = None  # for "make that 3" / quantity update commands
        self._shopping_mode_last_activity = None  # timestamp of last handled command in mode
        self._shopping_mode_timeout = self.plugin_config.get(
            "shopping_mode_timeout", PantryPlugin._SHOPPING_MODE_TIMEOUT_DEFAULT
        )

        self.register_system_prompt(
            "SHOPPING LIST & PANTRY: A shopping list and pantry inventory system is available. "
            "When the user says they are out of something, add it to the shopping list. "
            "When they store something, record its location in the pantry. "
            "When they mention an expiry date, record it. "
            "Use show_shopping_list or show_pantry to display interactive views on screen "
            "rather than reading long lists aloud. "
            "When the user asks about expiring items, suggest recipes or meals that use those ingredients."
        )

        self._register_shopping_list_tools()
        self._register_pantry_tools()
        self._register_recipe_integration_tools()

        # Register UI action handlers (SocketIO events from the display)
        self.register_ui_action("shopping_list_action", self._on_shopping_list_action)
        self.register_ui_action("pantry_action", self._on_pantry_action)

        # Register display views
        self.register_view("shopping_list", "plugins/pantry/views/shopping.js", css_path="plugins/pantry/views/shopping.css", dashboard_card=True)
        self.register_view("pantry", "plugins/pantry/views/pantry.js", dashboard_card=True)
        self.register_view("pantry_shelf_life", "plugins/pantry/views/pantry.js")
        self.register_view("pantry_put_away", "plugins/pantry/views/pantry.js")

        list_count = len(self._shopping_list["items"])
        pantry_count = len(self._pantry["items"])
        logger.success(f"[Pantry] Initialized — {list_count} shopping list items, {pantry_count} pantry items")

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _load_json(self, filename: str, default: dict) -> dict:
        path = os.path.join(self._data_dir, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[Pantry] Failed to load {filename}: {e}, using defaults")
        return default

    def _save_shopping_list(self):
        path = os.path.join(self._data_dir, "shopping_list.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._shopping_list, f, indent=2, ensure_ascii=False)

    def _save_pantry(self):
        path = os.path.join(self._data_dir, "pantry.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._pantry, f, indent=2, ensure_ascii=False)

    # -----------------------------------------------------------------------
    # Data migration
    # -----------------------------------------------------------------------

    def _migrate_pantry_data(self):
        """Migrate pantry data to current schema. Safe to run multiple times."""
        changed = False

        # Add "type" to locations missing it
        for loc in self._pantry["locations"]:
            if "type" not in loc:
                loc["type"] = _infer_location_type(loc["id"], loc["name"])
                changed = True

        # Add "expiry_source" to items
        for item in self._pantry["items"]:
            if "expiry_source" not in item:
                item["expiry_source"] = "user" if item.get("expires") else None
                changed = True

        # Ensure shelf_life_config exists
        if "shelf_life_config" not in self._pantry:
            self._pantry["shelf_life_config"] = {}
            changed = True

        if changed:
            self._save_pantry()
            logger.info("[Pantry] Migrated pantry data to current schema")

    # -----------------------------------------------------------------------
    # Shelf life estimation
    # -----------------------------------------------------------------------

    def _estimate_expiry(self, item_name: str, location_id: str) -> Optional[str]:
        """Estimate expiry date based on item category and storage location type.

        Resolution order: user UI overrides → config file overrides → hardcoded defaults.
        Returns ISO date string (YYYY-MM-DD) or None.
        """
        if not self.plugin_config.get("auto_estimate_expiry", True):
            return None

        loc = next((l for l in self._pantry["locations"] if l["id"] == location_id), None)
        if not loc:
            return None

        loc_type = loc.get("type", "room_temp")
        category = _categorize_item(item_name)
        # If the location itself is a spice rack, treat items as spices
        loc_name = loc.get("name", "").lower()
        if "spice" in loc_name and category not in ("spices",):
            category = "spices"
        # Ready meals use their own shelf life regardless of ingredient category
        item_type = _classify_item_type(item_name)
        if item_type == "ready_meal":
            category = "ready_meal"

        # Layer 1: user overrides from UI (stored in pantry.json)
        ui_overrides = self._pantry.get("shelf_life_config", {})
        if category in ui_overrides and loc_type in ui_overrides[category]:
            days = ui_overrides[category][loc_type]
        # Layer 2: config file overrides
        elif category in self.plugin_config.get("shelf_life_overrides", {}) \
                and loc_type in self.plugin_config["shelf_life_overrides"][category]:
            days = self.plugin_config["shelf_life_overrides"][category][loc_type]
        # Layer 3: hardcoded defaults
        elif category in DEFAULT_SHELF_LIFE and loc_type in DEFAULT_SHELF_LIFE[category]:
            days = DEFAULT_SHELF_LIFE[category][loc_type]
        else:
            days = DEFAULT_SHELF_LIFE.get("other", {}).get(loc_type, 7)

        if days <= 0:
            logger.debug(f"[Pantry] Shelf life for '{item_name}' ({category}) in {loc_type} is {days}d — not recommended")
            return None

        estimated = (date.today() + timedelta(days=days)).isoformat()
        logger.debug(f"[Pantry] Estimated expiry for '{item_name}' ({category}, {loc_type}): {estimated} ({days}d)")
        return estimated

    def _get_shelf_life_config(self) -> dict:
        """Get the merged shelf life config: defaults + config overrides + UI overrides."""
        merged = {}
        for category, loc_map in DEFAULT_SHELF_LIFE.items():
            merged[category] = dict(loc_map)
        # Apply config file overrides
        for category, loc_map in self.plugin_config.get("shelf_life_overrides", {}).items():
            if category not in merged:
                merged[category] = {}
            merged[category].update(loc_map)
        # Apply UI overrides (highest priority)
        for category, loc_map in self._pantry.get("shelf_life_config", {}).items():
            if category not in merged:
                merged[category] = {}
            merged[category].update(loc_map)
        return merged

    # -----------------------------------------------------------------------
    # Fuzzy matching helpers
    # -----------------------------------------------------------------------

    def _find_shopping_item(self, name: str) -> Optional[dict]:
        """Find a shopping list item by fuzzy name match.

        Uses partial_ratio so "chicken" matches "chicken breasts" and
        "milk" matches "full cream milk".
        """
        name_lower = name.lower()
        best, best_score = None, 0
        for item in self._shopping_list["items"]:
            score = fuzz.partial_ratio(name_lower, item["name"].lower())
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= 80 else None

    def _find_pantry_items(self, name: str, strict: bool = False) -> list[dict]:
        """Find pantry items by fuzzy name match. Returns all matches above threshold.

        Args:
            name: Item name to search for
            strict: If True, use fuzz.ratio (full string similarity) instead of
                    fuzz.partial_ratio (substring containment). Strict mode avoids
                    false matches like "chicken thighs" matching "chicken breasts".
        """
        name_lower = name.lower()
        scorer = fuzz.ratio if strict else fuzz.partial_ratio
        threshold = 80 if strict else 70
        matches = []
        for item in self._pantry["items"]:
            score = scorer(name_lower, item["name"].lower())
            if score >= threshold:
                matches.append(item)
        return matches

    def _find_location(self, name: str) -> Optional[dict]:
        """Find a storage location by fuzzy name match."""
        name_lower = name.lower()
        best, best_score = None, 0
        for loc in self._pantry["locations"]:
            score = fuzz.partial_ratio(name_lower, loc["name"].lower())
            if score > best_score:
                best, best_score = loc, score
        return best if best_score >= 65 else None

    def _location_name(self, location_id: str) -> str:
        """Get location display name from ID."""
        for loc in self._pantry["locations"]:
            if loc["id"] == location_id:
                return loc["name"]
        return location_id or "unassigned"

    # -----------------------------------------------------------------------
    # Tool registration
    # -----------------------------------------------------------------------

    def _register_shopping_list_tools(self):
        self.register_tool(
            handler=self.add_to_shopping_list,
            description=(
                "Add an item to the shopping list. Also use when the user says they "
                "are out of something or need to buy something."
            ),
            parameters={
                "item": {"type": "string", "description": "The item to add, e.g. 'eggs', 'whole milk'"},
                "quantity": {"type": "string", "description": "How much to buy, e.g. '1 dozen', '2 packs'"},
                "recurring_days": {
                    "type": "integer",
                    "description": "If set, automatically re-add this item every N days",
                },
            },
            required=["item"],
            intents=[
                "add eggs to the shopping list",
                "add milk to the shopping list",
                "add bread to the shopping list",
                "add butter to the shopping list",
                "add chicken to the shopping list",
                "put milk on the list",
                "put eggs on the list",
                "we need butter",
                "we need milk",
                "we're out of eggs",
                "we're out of milk",
                "we're out of bread",
                "add bread to the list",
                "we need to buy flour",
                "I need to get some chicken",
                "we're running low on rice",
                "we buy eggs every two weeks",
                "add that to the shopping list",
                "shopping list add",
            ],
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_add_to_list_nlp_extract,
            nlp_response=_add_to_list_nlp_response,
        )

        self.register_tool(
            handler=self.remove_from_shopping_list,
            description="Remove an item from the shopping list.",
            parameters={
                "item": {"type": "string", "description": "The item to remove"},
            },
            required=["item"],
            intents=[
                "remove milk from the shopping list",
                "remove eggs from the shopping list",
                "remove bread from the shopping list",
                "remove butter from the list",
                "take eggs off the list",
                "take milk off the list",
                "delete bread from the list",
                "delete milk from the list",
                "never mind the butter",
                "remove that from the list",
                "cross off eggs",
                "cross off milk",
            ],
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_remove_from_list_nlp_extract,
            nlp_response=_remove_from_list_nlp_response,
        )

        self.register_tool(
            handler=self.rename_last_shopping_item,
            description=(
                "Rename the last item added to the shopping list. "
                "Use when the user says the name is wrong or wants to use a different name. "
                "If no new_name is provided, reverts to the original name before normalization."
            ),
            parameters={
                "new_name": {
                    "type": "string",
                    "description": "The new name for the item. Omit to revert to the original name.",
                },
            },
            required=[],
            intents=[
                # Correction after add — specific to shopping list name errors
                "that's not what I said on the list",
                "I said chicken not chicken breast",
                "use the name I said for the shopping item",
                "rename that on the shopping list",
                "change the name on the list",
                "rename the last item on the list",
                "that's not what I added",
                "no I meant chicken on the list",
                "the shopping list item name is wrong",
                "undo the rename on the list",
                "revert the last shopping item name",
            ],
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
        )

        self.register_tool(
            handler=self.show_shopping_list,
            description=(
                "Show the shopping list on the display screen. "
                "Use this when the user asks what's on the list."
            ),
            parameters={},
            required=[],
            intents=[
                "what's on the shopping list",
                "what is on the shopping list",
                "show the shopping list",
                "show me the shopping list",
                "what do we need to buy",
                "what do I need to buy",
                "read me the shopping list",
                "show the list",
                "what do I need from the shops",
                "read the shopping list",
                "display the shopping list",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_response=_show_list_nlp_response,
        )

        self.register_tool(
            handler=self.complete_shopping,
            description=(
                "Mark shopping as complete. Moves bought items to the pantry. "
                "Optionally specify items NOT bought (they stay on the list)."
            ),
            parameters={
                "except_items": {
                    "type": "string",
                    "description": "Comma-separated items NOT bought, e.g. 'eggs, butter'",
                },
            },
            required=[],
            intents=[
                "we got everything on the list",
                "we got everything on the shopping list",
                "we got everything",
                "we did the shopping",
                "we've done the shopping",
                "we got everything except eggs and butter",
                "shopping done",
                "shopping is done",
                "finished shopping",
                "done shopping",
                "mark everything as bought",
                "we bought everything",
                "we bought everything except the milk",
                "we got it all",
                "we got it all except eggs",
                "completed the shopping",
                "the shopping is complete",
            ],
            process_output=False,
            activity=[Activity.GENERAL],
            nlp_extract_fn=_complete_shopping_nlp_extract,
            nlp_response=_complete_shopping_nlp_response,
        )

    def _register_pantry_tools(self):
        self.register_tool(
            handler=self.store_item,
            description=(
                "Record where an item is stored in the pantry. "
                "Use when the user says they put something somewhere."
            ),
            parameters={
                "item": {"type": "string", "description": "The item being stored"},
                "location": {"type": "string", "description": "Where it's being stored, e.g. 'freezer drawer 2'"},
                "notes": {"type": "string", "description": "Additional notes like quantity"},
                "expires": {"type": "string", "description": "Expiry date, e.g. 'March 24th', 'the 15th', '2026-04-01'"},
                "item_type": {"type": "string", "enum": ["ingredient", "ready_meal"],
                              "description": "Is this a raw ingredient or a complete ready-to-eat meal/dish?"},
            },
            required=["item", "location"],
            intents=[
                "I put the chicken in freezer drawer 2",
                "the flour is in the dry goods cupboard",
                "store the milk in the fridge",
                "put the bread in the pantry",
                "I stored the rice in the cupboard",
                "I put eggs in the fridge",
                "I added boiled eggs to the fridge",
                "put the butter in the refrigerator",
                "I put the leftovers in the freezer",
                "store the vegetables in the fridge",
                "I put the cheese in the fridge",
                "the jam is in the fridge door",
                "I stored the sauce in the cupboard",
            ],
            nlp_threshold=0.5,
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_store_item_nlp_extract,
            nlp_response=_store_item_nlp_response,
        )

        self.register_tool(
            handler=self.set_expiry,
            description=(
                "Set or update the expiry date of a pantry item. "
                "Use when the user mentions when something expires or its best-before date."
            ),
            parameters={
                "item": {"type": "string", "description": "The item name"},
                "expires": {"type": "string", "description": "Expiry date, e.g. 'the 24th', '26th June', 'tomorrow'"},
            },
            required=["item", "expires"],
            intents=[
                "the bacon expires on the 24th",
                "the chicken expires in five days",
                "the chicken expires in 5 days",
                "eggs are best before 26th june",
                "the milk expires tomorrow",
                "chicken use by friday",
                "yogurt best before the 15th",
                "the ham expires next week",
                "eggs expire tomorrow",
                "milk expires on monday",
                "the butter expires in three days",
                "beef expires on the 30th",
                "cheese best before the 10th",
            ],
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_set_expiry_nlp_extract,
            nlp_response=_set_expiry_nlp_response,
        )

        self.register_tool(
            handler=self.find_item,
            description=(
                "Find where an item is stored in the pantry, or check if we have it."
            ),
            parameters={
                "item": {"type": "string", "description": "The item to find"},
            },
            required=["item"],
            intents=[
                "where is the flour",
                "do we have eggs",
                "where did I put the chicken",
                "is there any butter",
                "where are the eggs",
                "check if we have rice",
                "find the chicken",
                "where is the butter",
                "where did I store the milk",
                "do we have any onions",
                "is there cheese in the fridge",
                "where are the leftovers",
                "what's in the fridge",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_find_item_nlp_extract,
            nlp_response=_find_item_nlp_response,
        )

        self.register_tool(
            handler=self.check_expiring,
            description=(
                "Check which pantry items are expiring soon. "
                "Returns items expiring within the specified number of days."
            ),
            parameters={
                "days": {
                    "type": "integer",
                    "description": "Number of days to look ahead (default 7)",
                },
            },
            required=[],
            intents=[
                "what's expiring soon",
                "what expires this week",
                "anything expiring",
                "check expiry dates",
                "what's going out of date",
                "what needs using up",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_check_expiring_nlp_extract,
            nlp_response=_check_expiring_nlp_response,
        )

        self.register_tool(
            handler=self.show_pantry,
            description="Show pantry contents on the display screen. Optionally filter by location.",
            parameters={
                "location": {"type": "string", "description": "Filter to a specific location, e.g. 'fridge'"},
            },
            required=[],
            intents=[
                "show me what's in the pantry",
                "what's in the fridge",
                "what's in freezer drawer 1",
                "show pantry contents",
                "what food do we have",
                "what's in the freezer",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=_show_pantry_nlp_extract,
            nlp_response=_show_pantry_nlp_response,
        )

        self.register_tool(
            handler=self.manage_pantry_locations,
            description="Add, remove, or rename storage locations in the pantry.",
            parameters={
                "action": {"type": "string", "enum": ["add", "remove", "rename"], "description": "The action"},
                "name": {"type": "string", "description": "The location name"},
                "new_name": {"type": "string", "description": "New name when renaming"},
            },
            required=["action", "name"],
            intents=[
                "add a location called garage freezer",
                "remove the dry goods cupboard location",
                "rename freezer drawer 1 to top freezer",
            ],
            process_output=False,
            activity=[Activity.GENERAL],
            nlp_extract_fn=_manage_locations_nlp_extract,
            nlp_response=_manage_locations_nlp_response,
        )

        self.register_tool(
            handler=self.set_item_type,
            description="Reclassify a pantry item as a ready meal or ingredient.",
            parameters={
                "item": {"type": "string", "description": "The pantry item name"},
                "item_type": {
                    "type": "string",
                    "enum": ["ingredient", "ready_meal"],
                    "description": "Classify as ingredient or ready_meal",
                },
            },
            required=["item", "item_type"],
            intents=[
                "mark the lasagna as a ready meal",
                "the chicken is a ready meal",
                "that's an ingredient not a meal",
                "mark that as a meal",
                "that's a ready meal",
                "classify the pizza as a meal",
            ],
            process_output=False,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_extract_fn=lambda text: {
                "item": re.sub(r"(?:mark|classify|set)\s+(?:the\s+)?(.+?)\s+as\s+.+", r"\1", text, flags=re.IGNORECASE).strip(),
                "item_type": "ready_meal" if "meal" in text.lower() else "ingredient",
            },
            nlp_response=lambda r: r.get("message", "Done."),
        )

        self.register_tool(
            handler=self.reclassify_pantry,
            description="Reclassify all pantry items as ingredients or ready meals.",
            intents=[
                "reclassify the pantry",
                "reclassify pantry items",
                "update pantry categories",
                "recategorize the pantry",
                "label everything in the pantry",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_response=lambda r: r.get("message", "Done."),
        )

        self.register_tool(
            handler=self.move_item,
            description=(
                "Move a pantry item to a different storage location. "
                "Use when the user says they moved something from one place to another. "
                "Only use this for items already in the pantry — for new items use store_item instead."
            ),
            parameters={
                "item": {"type": "string", "description": "The item to move"},
                "new_location": {"type": "string", "description": "The destination location, e.g. 'freezer drawer 1'"},
            },
            required=["item", "new_location"],
            activity=[Activity.GENERAL, Activity.COOKING],
        )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self):
        from glados.llm.chat_hooks import ChatPipelinePhase
        self.register_chat_hook(
            phase=ChatPipelinePhase.PRE_LLM,
            callback=self._shopping_context_hook,
            priority=5,  # Before memory (10), knowledge (15), etc.
        )
        self.event_system.subscribe(
            "ui.shopping_list_action",
            EventHook("shopping_list_ui", callback=self._on_shopping_list_action, priority=5),
        )
        self.event_system.subscribe(
            "ui.pantry_action",
            EventHook("pantry_ui", callback=self._on_pantry_action, priority=5),
        )
        self.event_system.subscribe(
            "system.tick",
            EventHook("pantry_recurring", callback=self._on_tick, priority=1),
        )
        # Track TTS state so proactive warnings don't clip active speech
        self._tts_active = False
        self.event_system.subscribe(
            "status.speaking",
            EventHook("pantry_tts_speaking", callback=lambda e: setattr(self, '_tts_active', True), priority=1),
        )
        self.event_system.subscribe(
            "status.idle",
            EventHook("pantry_tts_idle", callback=lambda e: setattr(self, '_tts_active', False), priority=1),
        )
        logger.info("[Pantry] Started — subscribed to UI events, tick, and shopping context hook")

    def stop(self):
        self._save_shopping_list()
        self._save_pantry()
        logger.info("[Pantry] Stopped — data saved")

    # -----------------------------------------------------------------------
    # Shopping list tools
    # -----------------------------------------------------------------------

    def _try_normalize_ingredient(self, item: str) -> tuple[str, str | None]:
        """Normalize an ingredient name if config allows. Returns (name, original_name_or_None)."""
        try:
            from glados.config import GladosConfig
            config = getattr(self, '_config', None)
            if config and not getattr(config, 'normalize_shopping_items', True):
                return item, None
        except Exception:
            pass

        try:
            from plugins.recipes.recipe_api import normalize_ingredient
            normalized, confidence = normalize_ingredient(item)
            if confidence >= 0.85 and normalized != item.lower().strip():
                return normalized, item
        except ImportError:
            pass
        return item, None

    def add_to_shopping_list(self, item: str, quantity: str = None, recurring_days: int = None) -> dict:
        """Add an item to the shopping list."""
        # Normalize ingredient name for better recipe matching
        normalized_name, original_name = self._try_normalize_ingredient(item)

        # Check if already on the list (check both original and normalized)
        existing = self._find_shopping_item(normalized_name) or (
            self._find_shopping_item(item) if original_name else None
        )
        if existing:
            if quantity:
                existing["quantity"] = quantity
                self._save_shopping_list()
            return {"status": "exists", "item": existing["name"],
                    "message": f"{existing['name']} is already on the shopping list."}

        new_item = {
            "id": uuid.uuid4().hex[:8],
            "name": normalized_name,
            "quantity": quantity,
            "category": _categorize_item(normalized_name),
            "added": datetime.now().isoformat(timespec="seconds"),
            "got": False,
        }
        if original_name:
            new_item["original_name"] = original_name
        self._shopping_list["items"].append(new_item)

        # Set up recurring rule if requested
        if recurring_days and recurring_days > 0:
            # Remove existing rule for this item
            self._shopping_list["recurring_rules"] = [
                r for r in self._shopping_list["recurring_rules"]
                if r["item_name"].lower() != normalized_name.lower()
            ]
            self._shopping_list["recurring_rules"].append({
                "item_name": normalized_name,
                "quantity": quantity,
                "interval_days": recurring_days,
                "next_due": (date.today() + timedelta(days=recurring_days)).isoformat(),
            })

        # If item is in pantry, remove it (they said they're out)
        pantry_matches = self._find_pantry_items(normalized_name)
        if not pantry_matches and original_name:
            pantry_matches = self._find_pantry_items(item)
        if pantry_matches:
            for pm in pantry_matches:
                self._pantry["items"].remove(pm)
            self._save_pantry()
            logger.info(f"[Pantry] Removed {len(pantry_matches)} pantry entries for '{normalized_name}' (user is out)")

        self._save_shopping_list()
        self._publish_shopping_list_display()
        self._last_added_item = new_item
        if original_name:
            logger.info(f"[Pantry] Added '{normalized_name}' (normalized from '{item}') to shopping list (category: {new_item['category']})")
        else:
            logger.info(f"[Pantry] Added '{normalized_name}' to shopping list (category: {new_item['category']})")

        # If category is "other", try LLM classification in background
        if new_item["category"] == "other":
            import threading
            def _bg_classify():
                try:
                    client, model = self._get_llm_classifier()
                    if not client:
                        return
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": (
                                "Classify this grocery item into exactly one category. "
                                "Reply with ONLY the category name.\n"
                                "Categories: dairy, produce, meat, seafood, bakery, frozen, "
                                "dry_goods, beverages, condiments, spices, snacks, other"
                            )},
                            {"role": "user", "content": item},
                        ],
                        max_tokens=10,
                        temperature=0,
                    )
                    cat = response.choices[0].message.content.strip().lower().replace(" ", "_")
                    valid = {"dairy", "produce", "meat", "seafood", "bakery", "frozen",
                             "dry_goods", "beverages", "condiments", "spices", "snacks"}
                    if cat in valid:
                        new_item["category"] = cat
                        self._save_shopping_list()
                        self._publish_shopping_list_display()
                        logger.info(f"[Pantry] LLM reclassified shopping item '{item}' → {cat}")
                except Exception as e:
                    logger.debug(f"[Pantry] Shopping item LLM classify failed: {e}")
            threading.Thread(target=_bg_classify, daemon=True).start()

        result = {
            "status": "added", "item": normalized_name, "category": new_item["category"],
            "recurring_days": recurring_days,
            "count": len(self._shopping_list["items"]),
        }
        if original_name:
            result["original_name"] = original_name
            result["message"] = f"Added {normalized_name} to the shopping list."
        return result

    def rename_last_shopping_item(self, new_name: str = None) -> dict:
        """Rename the last added shopping list item, or revert to the original name."""
        if not self._last_added_item:
            return {"status": "error", "message": "No recently added item to rename."}

        item = self._last_added_item
        old_name = item["name"]

        if not new_name:
            # Revert to original name if available
            original = item.get("original_name")
            if original:
                new_name = original
            else:
                return {"status": "error", "message": "No original name to revert to."}

        item["name"] = new_name
        item["category"] = _categorize_item(new_name)
        item.pop("original_name", None)
        self._save_shopping_list()
        self._publish_shopping_list_display()
        logger.info(f"[Pantry] Renamed shopping item '{old_name}' → '{new_name}'")
        return {
            "status": "success",
            "old_name": old_name,
            "new_name": new_name,
            "message": f"Renamed {old_name} to {new_name} on the shopping list.",
        }

    def remove_from_shopping_list(self, item: str) -> dict:
        """Remove an item from the shopping list."""
        match = self._find_shopping_item(item)
        if not match:
            return {"status": "not_found", "message": f"'{item}' is not on the shopping list."}

        self._shopping_list["items"].remove(match)
        self._save_shopping_list()
        self._publish_shopping_list_display()
        logger.info(f"[Pantry] Removed '{match['name']}' from shopping list")
        return {"status": "success", "item": match["name"]}

    def show_shopping_list(self) -> dict:
        """Show the shopping list on the display."""
        items = self._shopping_list["items"]
        self._publish_shopping_list_display()
        return {
            "status": "success",
            "count": len(items),
            "items": [{"name": i["name"], "quantity": i["quantity"], "category": i["category"]} for i in items],
        }

    def complete_shopping(self, except_items: str = None) -> dict:
        """Complete shopping — move bought (checked) items to pantry.

        Items marked as 'got' are moved. Unchecked items stay on the list.
        The except_items parameter (from voice) explicitly keeps named items.
        """
        except_names = []
        if except_items:
            except_names = [n.strip().lower() for n in re.split(r"\s*,\s*", except_items)]

        moved = []
        remaining = []
        # If no items are checked (voice command without UI interaction), move all
        any_checked = any(i.get("got") for i in self._shopping_list["items"])

        for item in list(self._shopping_list["items"]):
            is_excepted = any(
                fuzz.partial_ratio(item["name"].lower(), exc) >= 80 for exc in except_names
            )
            is_unchecked = any_checked and not item.get("got", False)
            if is_excepted or is_unchecked:
                item["got"] = False
                remaining.append(item)
            else:
                # Move to pantry (location unassigned)
                pantry_item = {
                    "id": uuid.uuid4().hex[:8],
                    "name": item["name"],
                    "location_id": None,
                    "stored": datetime.now().isoformat(timespec="seconds"),
                    "expires": None,
                    "expiry_source": None,
                    "notes": item.get("quantity"),
                    "item_type": _classify_item_type(item["name"]),
                }
                self._pantry["items"].append(pantry_item)
                moved.append(item)

        # Update shopping list to only keep excepted items
        self._shopping_list["items"] = remaining
        self._save_shopping_list()
        self._save_pantry()
        self._publish_shopping_list_display()

        logger.info(f"[Pantry] Shopping complete — moved {len(moved)}, {len(remaining)} remaining")

        # Show put-away view if there are unassigned items
        unassigned = [i for i in self._pantry["items"] if not i.get("location_id")]
        if unassigned:
            self._publish_put_away_display()

        return {
            "status": "success",
            "moved": len(moved),
            "remaining": len(remaining),
            "remaining_items": [i["name"] for i in remaining],
        }

    # -----------------------------------------------------------------------
    # Pantry tools
    # -----------------------------------------------------------------------

    def store_item(self, item: str, location: str, notes: str = None, expires: str = None,
                   item_type: str = None) -> dict:
        """Record where an item is stored in the pantry."""
        loc = self._find_location(location)
        if not loc:
            return {"status": "error", "message": f"Unknown location '{location}'. Use manage_pantry_locations to add it."}

        expires_date = None
        expiry_source = None
        if expires:
            expires_date = _parse_expiry_date(expires)
            if expires_date:
                expiry_source = "user"

        # Auto-estimate expiry if none provided
        if not expires_date:
            expires_date = self._estimate_expiry(item, loc["id"])
            if expires_date:
                expiry_source = "estimated"

        # Classify item type: explicit > keyword auto-detect
        effective_type = item_type or _classify_item_type(item)

        # Check if item already exists in this location — update instead of duplicate
        existing = None
        for pi in self._pantry["items"]:
            if fuzz.ratio(pi["name"].lower(), item.lower()) >= 70 and pi["location_id"] == loc["id"]:
                existing = pi
                break

        if existing:
            if notes:
                existing["notes"] = notes
            if expires_date:
                existing["expires"] = expires_date
                existing["expiry_source"] = expiry_source
            existing["stored"] = datetime.now().isoformat(timespec="seconds")
            existing["item_type"] = item_type or existing.get("item_type") or effective_type
        else:
            self._pantry["items"].append({
                "id": uuid.uuid4().hex[:8],
                "name": item,
                "location_id": loc["id"],
                "stored": datetime.now().isoformat(timespec="seconds"),
                "expires": expires_date,
                "expiry_source": expiry_source,
                "notes": notes,
                "item_type": effective_type,
            })

        # Background LLM classification if available (fire-and-forget)
        if not item_type:
            self._bg_classify_item(item, loc["id"])

        # Remove from shopping list if present
        shopping_match = self._find_shopping_item(item)
        if shopping_match:
            self._shopping_list["items"].remove(shopping_match)
            self._save_shopping_list()

        self._save_pantry()
        logger.info(f"[Pantry] Stored '{item}' in {loc['name']} (type={effective_type})")
        return {"status": "success", "item": item, "location": loc["name"], "item_type": effective_type}

    def move_item(self, item: str, new_location: str) -> dict:
        """Move a pantry item to a different storage location."""
        matches = self._find_pantry_items(item)
        if not matches:
            return {"status": "error", "message": f"'{item}' is not in the pantry."}

        loc = self._find_location(new_location)
        if not loc:
            return {"status": "error", "message": f"Unknown location '{new_location}'."}

        moved = matches[0]
        old_loc = next((l for l in self._pantry["locations"] if l["id"] == moved.get("location_id")), None)
        old_name = old_loc["name"] if old_loc else "unknown"

        moved["location_id"] = loc["id"]

        # Re-estimate expiry if the current one was auto-estimated
        if moved.get("expiry_source") == "estimated" or not moved.get("expires"):
            new_estimate = self._estimate_expiry(moved["name"], loc["id"])
            if new_estimate:
                moved["expires"] = new_estimate
                moved["expiry_source"] = "estimated"
                logger.info(f"[Pantry] Re-estimated expiry for '{moved['name']}' after move: {new_estimate}")

        self._save_pantry()
        self._publish_pantry_display()
        logger.info(f"[Pantry] Moved '{moved['name']}' from {old_name} to {loc['name']}")
        return {
            "status": "success",
            "item": moved["name"],
            "from": old_name,
            "to": loc["name"],
            "message": f"Moved {moved['name']} from {old_name} to {loc['name']}.",
        }

    def _get_llm_classifier(self):
        """Get the fast LLM client + model for item classification. Returns (client, model) or (None, None)."""
        try:
            from glados.config import GladosConfig
            config = GladosConfig.from_yaml("glados_config.yml")
            model = getattr(config, "knowledge_rewrite_model", None)
            url = getattr(config, "knowledge_rewrite_url", None) or getattr(config, "completion_url", "")
            if not model:
                return None, None
            from openai import OpenAI
            return OpenAI(base_url=url, api_key="not-needed", timeout=5.0), model
        except Exception:
            return None, None

    def _llm_classify_item(self, client, model, item_name: str) -> str | None:
        """Classify a single item via the fast LLM. Returns 'ingredient' or 'ready_meal' or None."""
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": (
                        'Classify this food item as either "ingredient" or "ready_meal".\n'
                        'An ingredient is a raw/component item (chicken, flour, eggs, butter, milk).\n'
                        'A ready_meal is a complete dish ready to eat/heat (lasagna, frozen pizza, '
                        'leftover soup, chicken tikka, shepherd\'s pie).\n'
                        'Reply with ONLY "ingredient" or "ready_meal".'
                    )},
                    {"role": "user", "content": item_name},
                ],
                max_tokens=10,
                temperature=0.0,
            )
            result = response.choices[0].message.content.strip().lower()
            return result if result in ("ingredient", "ready_meal") else None
        except Exception as e:
            logger.debug(f"[Pantry] LLM classify failed for '{item_name}': {e}")
            return None

    def _bg_classify_item(self, item_name: str, location_id: str):
        """Background LLM classification for a single item (fire-and-forget)."""
        import threading

        def _classify():
            client, model = self._get_llm_classifier()
            if not client:
                return
            result = self._llm_classify_item(client, model, item_name)
            if result:
                for pi in self._pantry["items"]:
                    if pi["name"].lower() == item_name.lower() and pi["location_id"] == location_id:
                        if pi.get("item_type") != result:
                            pi["item_type"] = result
                            self._save_pantry()
                            logger.info(f"[Pantry] LLM reclassified '{item_name}' as {result}")
                        break

        threading.Thread(target=_classify, daemon=True).start()

    def reclassify_pantry(self) -> dict:
        """Reclassify all pantry items. Uses LLM if available, otherwise keyword matching."""
        client, model = self._get_llm_classifier()
        use_llm = client is not None
        total = len(self._pantry["items"])

        # Publish start event for UI progress
        self.event_system.publish(EventMessage(
            "status", "reclassify_progress", {"current": 0, "total": total, "phase": "start"}
        ))

        updated = 0
        for idx, item in enumerate(self._pantry["items"]):
            old_type = item.get("item_type")
            if use_llm:
                new_type = self._llm_classify_item(client, model, item["name"])
            else:
                new_type = _classify_item_type(item["name"])
            if new_type and new_type != old_type:
                item["item_type"] = new_type
                updated += 1
                logger.info(f"[Pantry] Reclassified '{item['name']}': {old_type} -> {new_type}")

            # Publish progress every item (LLM is slow, so each tick matters)
            self.event_system.publish(EventMessage(
                "status", "reclassify_progress",
                {"current": idx + 1, "total": total, "item": item["name"], "phase": "running"}
            ))

        if updated:
            self._save_pantry()
            self._publish_pantry_display()

        method = "LLM" if use_llm else "keyword"
        logger.info(f"[Pantry] Reclassification complete: {updated}/{total} changed ({method})")

        self.event_system.publish(EventMessage(
            "status", "reclassify_progress",
            {"current": total, "total": total, "updated": updated, "phase": "done"}
        ))

        return {
            "status": "success",
            "total": total,
            "updated": updated,
            "method": method,
            "message": f"Reclassified {total} items ({updated} changed) using {method}.",
        }

    def set_item_type(self, item: str, item_type: str) -> dict:
        """Reclassify a pantry item as ingredient or ready meal."""
        if item_type not in ("ingredient", "ready_meal"):
            return {"status": "error", "message": "Type must be 'ingredient' or 'ready_meal'."}
        matches = self._find_pantry_items(item)
        if not matches:
            return {"status": "not_found", "message": f"'{item}' is not in the pantry."}
        matches[0]["item_type"] = item_type
        self._save_pantry()
        label = "a ready meal" if item_type == "ready_meal" else "an ingredient"
        logger.info(f"[Pantry] Reclassified '{matches[0]['name']}' as {item_type}")
        return {"status": "success", "item": matches[0]["name"], "item_type": item_type,
                "message": f"Marked {matches[0]['name']} as {label}."}

    def set_expiry(self, item: str, expires: str) -> dict:
        """Set or update the expiry date of a pantry item."""
        matches = self._find_pantry_items(item)
        if not matches:
            return {"status": "not_found", "message": f"'{item}' is not in the pantry."}

        expires_date = _parse_expiry_date(expires)
        if not expires_date:
            return {"status": "error", "message": f"Could not parse date '{expires}'."}

        for m in matches:
            m["expires"] = expires_date
        self._save_pantry()

        logger.info(f"[Pantry] Set expiry for '{item}' to {expires_date}")
        return {"status": "success", "item": matches[0]["name"], "expires": expires_date}

    def find_item(self, item: str) -> dict:
        """Find where an item is stored in the pantry."""
        matches = self._find_pantry_items(item)
        if matches:
            return {
                "status": "found",
                "matches": [
                    {
                        "name": m["name"],
                        "location": self._location_name(m["location_id"]),
                        "expires": m.get("expires"),
                        "notes": m.get("notes"),
                    }
                    for m in matches
                ],
            }
        # Check if it's on the shopping list
        on_list = self._find_shopping_item(item) is not None
        return {"status": "not_found", "item": item, "on_shopping_list": on_list}

    def check_expiring(self, days: int = 7) -> dict:
        """Check which pantry items are expiring within N days."""
        today = date.today()
        cutoff = today + timedelta(days=days)
        expiring = []

        for item in self._pantry["items"]:
            if item.get("expires"):
                try:
                    exp_date = date.fromisoformat(item["expires"])
                    if exp_date <= cutoff:
                        expiring.append({
                            "name": item["name"],
                            "location": self._location_name(item["location_id"]),
                            "expires": item["expires"],
                            "days_left": (exp_date - today).days,
                        })
                except ValueError:
                    continue

        expiring.sort(key=lambda x: x["days_left"])
        self._publish_pantry_display()
        return {"status": "success", "expiring": expiring, "days": days}

    def show_pantry(self, location: str = None) -> dict:
        """Show pantry contents on the display."""
        items = self._pantry["items"]
        location_filter = None

        if location:
            loc = self._find_location(location)
            if loc:
                items = [i for i in items if i["location_id"] == loc["id"]]
                location_filter = loc["name"]

        self._publish_pantry_display(location_filter=location)
        return {
            "status": "success",
            "total_items": len(items),
            "location_filter": location_filter,
            "items": [
                {
                    "name": i["name"],
                    "location": self._location_name(i["location_id"]),
                    "expires": i.get("expires"),
                    "notes": i.get("notes"),
                }
                for i in items
            ],
        }

    def manage_pantry_locations(self, action: str, name: str, new_name: str = None) -> dict:
        """Add, remove, or rename storage locations."""
        if action == "add":
            loc_id = name.lower().replace(" ", "-")
            if any(l["id"] == loc_id for l in self._pantry["locations"]):
                return {"status": "exists", "message": f"Location '{name}' already exists."}
            self._pantry["locations"].append({
                "id": loc_id, "name": name, "type": _infer_location_type(loc_id, name),
            })
            self._save_pantry()
            return {"status": "success", "message": f"Added location '{name}'."}

        elif action == "remove":
            loc = self._find_location(name)
            if not loc:
                return {"status": "not_found", "message": f"Location '{name}' not found."}
            # Move items to unassigned
            for item in self._pantry["items"]:
                if item["location_id"] == loc["id"]:
                    item["location_id"] = None
            self._pantry["locations"].remove(loc)
            self._save_pantry()
            return {"status": "success", "message": f"Removed location '{loc['name']}'."}

        elif action == "rename":
            loc = self._find_location(name)
            if not loc:
                return {"status": "not_found", "message": f"Location '{name}' not found."}
            old_name = loc["name"]
            loc["name"] = new_name
            self._save_pantry()
            return {"status": "success", "message": f"Renamed '{old_name}' to '{new_name}'."}

        return {"status": "error", "message": f"Unknown action '{action}'."}

    # -----------------------------------------------------------------------
    # Display publishing
    # -----------------------------------------------------------------------

    def _publish_shopping_list_display(self):
        """Push shopping list view to the display."""
        items = self._shopping_list["items"]
        got_count = sum(1 for i in items if i.get("got"))
        recurring_map = {r["item_name"].lower(): r.get("interval_days", 0) for r in self._shopping_list.get("recurring_rules", [])}
        unassigned_count = sum(1 for i in self._pantry["items"] if not i.get("location_id"))

        # Include core ingredient names for client-side autocomplete
        try:
            from plugins.recipes.recipe_api import _core_ingredient_list
            known_ingredients = _core_ingredient_list
        except ImportError:
            known_ingredients = []

        self.event_system.publish(EventMessage(
            role="display",
            name="shopping_list",
            content={
                "view_type": "shopping_list",
                "title": "Shopping List",
                "items": [
                    {
                        "id": i["id"],
                        "name": i["name"],
                        "quantity": i.get("quantity"),
                        "category": i.get("category", "other"),
                        "got": i.get("got", False),
                        "recurring": i["name"].lower() in recurring_map,
                        "recurring_days": recurring_map.get(i["name"].lower(), 0),
                    }
                    for i in items
                ],
                "total": len(items),
                "got_count": got_count,
                "unassigned_count": unassigned_count,
                "known_ingredients": known_ingredients,
            },
            process_output=False,
        ))

        # Inject display state for LLM context
        item_names = [i["name"] for i in items[:10]]
        self.event_system.publish(EventMessage(
            "tool", "display_state",
            f"<display_state>The user is viewing their shopping list with {len(items)} items "
            f"({got_count} checked). Items: {', '.join(item_names)}{'...' if len(items) > 10 else ''}.</display_state>",
            process_output=False,
        ))

    def _publish_dashboard_summary(self):
        """Push summary counts for the dashboard cards."""
        today = date.today()
        expiring = []
        for item in self._pantry["items"]:
            if item.get("expires"):
                try:
                    exp = date.fromisoformat(item["expires"])
                    days_left = (exp - today).days
                    if days_left <= 7:
                        expiring.append({"name": item["name"], "days_left": days_left})
                except ValueError:
                    pass
        expiring.sort(key=lambda x: x["days_left"])

        recurring_count = len(self._shopping_list.get("recurring_rules", []))
        self.event_system.publish(EventMessage(
            role="display",
            name="dashboard_data",
            content={
                "view_type": "dashboard_data",
                "shopping": {
                    "count": len(self._shopping_list["items"]),
                    "got": sum(1 for i in self._shopping_list["items"] if i.get("got")),
                    "recurring": recurring_count,
                },
                "pantry": {
                    "count": len(self._pantry["items"]),
                    "expiring": expiring,
                },
            },
            process_output=False,
        ))

    def _publish_put_away_display(self):
        """Publish the put-away guided view for unassigned items."""
        unassigned = [i for i in self._pantry["items"] if not i.get("location_id")]
        if not unassigned:
            return

        locations = [{"id": l["id"], "name": l["name"], "type": l.get("type", "room_temp")}
                     for l in self._pantry["locations"]]

        items = []
        for item in unassigned:
            category = _categorize_item(item["name"])
            suggested_type = CATEGORY_LOCATION_SUGGESTION.get(category, "fridge")
            # Find the first location matching the suggested type
            suggested_loc = next(
                (l for l in self._pantry["locations"] if l.get("type") == suggested_type), None
            )
            items.append({
                "id": item["id"],
                "name": item["name"],
                "category": category,
                "item_type": item.get("item_type", "ingredient"),
                "notes": item.get("notes"),
                "suggested_location_id": suggested_loc["id"] if suggested_loc else None,
                "suggested_location_name": suggested_loc["name"] if suggested_loc else None,
            })

        self.event_system.publish(EventMessage(
            role="display", name="pantry_put_away",
            content={"items": items, "locations": locations},
            process_output=False,
        ))

    def _publish_pantry_display(self, location_filter: str = None):
        """Push pantry view to the display."""
        locations_data = []
        for loc in self._pantry["locations"]:
            loc_items = [i for i in self._pantry["items"] if i["location_id"] == loc["id"]]
            if location_filter:
                filter_loc = self._find_location(location_filter)
                if filter_loc and filter_loc["id"] != loc["id"]:
                    continue
            locations_data.append({
                "id": loc["id"],
                "name": loc["name"],
                "type": loc.get("type", "room_temp"),
                "items": [
                    {
                        "id": i["id"],
                        "name": i["name"],
                        "notes": i.get("notes"),
                        "expires": i.get("expires"),
                        "expiry_source": i.get("expiry_source"),
                        "stored": i.get("stored", "")[:10],
                        "item_type": i.get("item_type"),
                        "category": _categorize_item(i["name"]),
                    }
                    for i in loc_items
                ],
            })

        # Add unassigned items
        unassigned = [i for i in self._pantry["items"] if not i["location_id"]]
        if unassigned:
            locations_data.append({
                "id": "_unassigned",
                "name": "Unassigned",
                "type": "room_temp",
                "items": [
                    {
                        "id": i["id"],
                        "name": i["name"],
                        "notes": i.get("notes"),
                        "expires": i.get("expires"),
                        "expiry_source": i.get("expiry_source"),
                        "stored": i.get("stored", "")[:10],
                        "item_type": i.get("item_type"),
                        "category": _categorize_item(i["name"]),
                    }
                    for i in unassigned
                ],
            })

        # Find expiring items for banner
        today = date.today()
        expiring_soon = []
        for item in self._pantry["items"]:
            if item.get("expires"):
                try:
                    exp = date.fromisoformat(item["expires"])
                    days_left = (exp - today).days
                    if days_left <= 3:
                        expiring_soon.append({
                            "id": item["id"],
                            "name": item["name"],
                            "location": self._location_name(item["location_id"]),
                            "days_left": days_left,
                        })
                except ValueError:
                    pass
        expiring_soon.sort(key=lambda x: x["days_left"])

        self.event_system.publish(EventMessage(
            role="display",
            name="pantry",
            content={
                "view_type": "pantry",
                "title": "Pantry",
                "locations": locations_data,
                "expiring_soon": expiring_soon,
            },
            process_output=False,
        ))

        # Inject display state for LLM context
        total = sum(len(loc.get("items", [])) for loc in locations_data)
        exp_count = len(expiring_soon)
        self.event_system.publish(EventMessage(
            "tool", "display_state",
            f"<display_state>The user is viewing the pantry with {total} items across "
            f"{len(locations_data)} locations. {exp_count} items expiring soon.</display_state>",
            process_output=False,
        ))

    # -----------------------------------------------------------------------
    # Shopping sub-context (planning / post-shopping / catalog modes)
    # Implementation lives in plugins/pantry/shopping_context.py (mixin).
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # UI event handlers
    # -----------------------------------------------------------------------

    def _on_shopping_list_action(self, event: EventMessage):
        """Handle interactive actions from the shopping list display."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")

        if action == "show":
            self._publish_shopping_list_display()
            return

        if action == "get_state":
            # Dashboard data request — don't navigate, just send counts
            self._publish_dashboard_summary()
            return

        if action == "toggle":
            item_id = data.get("item_id")
            for item in self._shopping_list["items"]:
                if item["id"] == item_id:
                    item["got"] = not item.get("got", False)
                    break
            self._save_shopping_list()
            self._publish_shopping_list_display()

        elif action == "remove":
            item_id = data.get("item_id")
            # Also remove any recurring rule for this item
            removed_item = next((i for i in self._shopping_list["items"] if i["id"] == item_id), None)
            self._shopping_list["items"] = [
                i for i in self._shopping_list["items"] if i["id"] != item_id
            ]
            if removed_item:
                self._shopping_list["recurring_rules"] = [
                    r for r in self._shopping_list["recurring_rules"]
                    if r["item_name"].lower() != removed_item["name"].lower()
                ]
            self._save_shopping_list()
            self._publish_shopping_list_display()

        elif action == "complete":
            self.complete_shopping()

        elif action == "set_recurring":
            # Set or remove recurring rule for an item: {item_id, interval_days} (0 = remove)
            item_id = data.get("item_id")
            interval = data.get("interval_days", 0)
            item = next((i for i in self._shopping_list["items"] if i["id"] == item_id), None)
            if item:
                # Remove existing rule
                self._shopping_list["recurring_rules"] = [
                    r for r in self._shopping_list["recurring_rules"]
                    if r["item_name"].lower() != item["name"].lower()
                ]
                if interval and interval > 0:
                    self._shopping_list["recurring_rules"].append({
                        "item_name": item["name"],
                        "quantity": item.get("quantity"),
                        "interval_days": interval,
                        "next_due": (date.today() + timedelta(days=interval)).isoformat(),
                    })
                    logger.info(f"[Pantry] Set recurring for '{item['name']}': every {interval} days")
                else:
                    logger.info(f"[Pantry] Removed recurring for '{item['name']}'")
                self._save_shopping_list()
                self._publish_shopping_list_display()

        elif action == "set_quantity":
            # Update quantity for an item: {item_id, quantity}
            item_id = data.get("item_id")
            quantity = data.get("quantity", "").strip() or None
            for item in self._shopping_list["items"]:
                if item["id"] == item_id:
                    item["quantity"] = quantity
                    break
            self._save_shopping_list()
            self._publish_shopping_list_display()

        elif action == "add_item":
            # Add item from UI: {name, quantity, category}
            name = data.get("name", "").strip()
            if name:
                self.add_to_shopping_list(
                    item=name,
                    quantity=data.get("quantity", "").strip() or None,
                )
                self._publish_shopping_list_display()

        elif action == "edit_item":
            # Edit item name and/or quantity from UI
            item_id = data.get("item_id")
            new_name = data.get("name", "").strip()
            for item in self._shopping_list["items"]:
                if item["id"] == item_id:
                    if new_name:
                        item["name"] = new_name
                        item["category"] = _categorize_item(new_name)
                    # Only update quantity if explicitly provided in the request
                    if "quantity" in data:
                        item["quantity"] = data["quantity"].strip() or None
                    break
            self._save_shopping_list()
            self._publish_shopping_list_display()

    def _on_pantry_action(self, event: EventMessage):
        """Handle interactive actions from the pantry display."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")
        logger.debug(f"[Pantry] UI action: {action} data={data}")

        if action == "show":
            self._publish_pantry_display()
            return

        if action == "get_summary":
            self._publish_dashboard_summary()
            return

        if action == "remove_item":
            item_id = data.get("item_id")
            self._pantry["items"] = [i for i in self._pantry["items"] if i["id"] != item_id]
            self._save_pantry()
            self._publish_pantry_display()

        elif action == "add_location":
            name = data.get("name", "").strip()
            if name:
                self.manage_pantry_locations("add", name)
                self._publish_pantry_display()

        elif action == "remove_location":
            loc_id = data.get("location_id")
            loc = next((l for l in self._pantry["locations"] if l["id"] == loc_id), None)
            if loc:
                self.manage_pantry_locations("remove", loc["name"])
                self._publish_pantry_display()

        elif action == "add_item":
            # Add item directly to a location from the UI
            name = data.get("name", "").strip()
            location_id = data.get("location_id")
            if name and location_id:
                expires = data.get("expires", "").strip() or None
                notes = data.get("notes", "").strip() or None
                expiry_source = "user" if expires else None
                if not expires:
                    expires = self._estimate_expiry(name, location_id)
                    if expires:
                        expiry_source = "estimated"
                self._pantry["items"].append({
                    "id": uuid.uuid4().hex[:8],
                    "name": name,
                    "location_id": location_id,
                    "stored": datetime.now().isoformat(timespec="seconds"),
                    "expires": expires,
                    "expiry_source": expiry_source,
                    "notes": notes,
                    "item_type": _classify_item_type(name),
                })
                # Remove from shopping list if present
                shopping_match = self._find_shopping_item(name)
                if shopping_match:
                    self._shopping_list["items"].remove(shopping_match)
                    self._save_shopping_list()
                self._save_pantry()
                self._publish_pantry_display()
                logger.info(f"[Pantry] UI: added '{name}' to location '{location_id}'")

        elif action == "set_expiry":
            # Set or clear expiry on an item from the UI
            item_id = data.get("item_id")
            expires = data.get("expires", "").strip() or None
            for item in self._pantry["items"]:
                if item["id"] == item_id:
                    item["expires"] = expires
                    item["expiry_source"] = "user"
                    logger.info(f"[Pantry] UI: set expiry for '{item['name']}' to {expires}")
                    break
            self._save_pantry()
            self._publish_pantry_display()

        elif action == "adjust_expiry":
            # Quick-adjust expiry by adding days
            item_id = data.get("item_id")
            days = data.get("days", 7)
            for item in self._pantry["items"]:
                if item["id"] == item_id:
                    base = date.today()
                    if item.get("expires"):
                        try:
                            base = date.fromisoformat(item["expires"])
                        except ValueError:
                            pass
                    new_expiry = (base + timedelta(days=days)).isoformat()
                    item["expires"] = new_expiry
                    item["expiry_source"] = "user"
                    logger.info(f"[Pantry] UI: adjusted expiry for '{item['name']}' +{days}d → {new_expiry}")
                    break
            self._save_pantry()
            self._publish_pantry_display()

        elif action == "edit_item":
            # Edit pantry item name/notes from UI
            item_id = data.get("item_id")
            new_name = data.get("name", "").strip()
            new_notes = data.get("notes", "").strip() or None
            for item in self._pantry["items"]:
                if item["id"] == item_id:
                    if new_name:
                        item["name"] = new_name
                    item["notes"] = new_notes
                    logger.info(f"[Pantry] UI: edited item '{new_name}'")
                    break
            self._save_pantry()
            self._publish_pantry_display()

        elif action == "move_item":
            item_id = data.get("item_id")
            new_location_id = data.get("new_location_id")
            logger.info(f"[Pantry] UI move: item_id={item_id} -> location={new_location_id}")
            if item_id and new_location_id:
                item = next((i for i in self._pantry["items"] if i["id"] == item_id), None)
                loc = next((l for l in self._pantry["locations"] if l["id"] == new_location_id), None)
                if item and loc:
                    old_loc_id = item.get("location_id")
                    item["location_id"] = new_location_id
                    # Re-estimate expiry if it was auto-estimated or missing
                    if item.get("expiry_source") == "estimated" or not item.get("expires"):
                        new_exp = self._estimate_expiry(item["name"], new_location_id)
                        if new_exp:
                            item["expires"] = new_exp
                            item["expiry_source"] = "estimated"
                    self._save_pantry()
                    self._publish_pantry_display()
                    logger.info(f"[Pantry] UI: moved '{item['name']}' from {old_loc_id} to {new_location_id}")

        elif action == "reclassify":
            import threading
            def _do_reclassify():
                result = self.reclassify_pantry()
                logger.info(f"[Pantry] UI reclassify: {result.get('message')}")
            threading.Thread(target=_do_reclassify, daemon=True).start()

        elif action == "check_recipe":
            recipe_name = data.get("recipe_name", "")
            if recipe_name:
                result = self.check_recipe_ingredients(recipe_name)
                # Speak the result
                msg = result.get("message", "Could not check ingredients.")
                self.event_system.publish(EventMessage("tts", "speak", msg))

        elif action == "add_recipe_to_list":
            recipe_name = data.get("recipe_name", "")
            if recipe_name:
                self.add_recipe_ingredients_to_list(recipe_name)
                # No TTS — user clicked a UI button, they can see the shopping list update

        elif action == "get_shelf_life_config":
            merged = self._get_shelf_life_config()
            self.event_system.publish(EventMessage(
                role="display", name="pantry_shelf_life",
                content={"config": merged},
                process_output=False,
            ))

        elif action == "update_shelf_life":
            config = data.get("config", {})
            self._pantry["shelf_life_config"] = config
            self._save_pantry()
            logger.info(f"[Pantry] UI: updated shelf life config ({len(config)} categories)")

        elif action == "set_location_type":
            loc_id = data.get("location_id")
            loc_type = data.get("type")
            if loc_id and loc_type in ("fridge", "freezer", "room_temp"):
                for loc in self._pantry["locations"]:
                    if loc["id"] == loc_id:
                        loc["type"] = loc_type
                        break
                self._save_pantry()
                self._publish_pantry_display()

        elif action == "assign_location":
            # Put-away flow: assign a location to an unassigned item
            item_id = data.get("item_id")
            location_id = data.get("location_id")
            if item_id and location_id:
                item = next((i for i in self._pantry["items"] if i["id"] == item_id), None)
                if item:
                    item["location_id"] = location_id
                    # Auto-estimate expiry now that we have a location
                    if not item.get("expires") or item.get("expiry_source") != "user":
                        est = self._estimate_expiry(item["name"], location_id)
                        if est:
                            item["expires"] = est
                            item["expiry_source"] = "estimated"
                    self._save_pantry()
                    # Refresh put-away view (shows remaining unassigned items)
                    unassigned = [i for i in self._pantry["items"] if not i.get("location_id")]
                    if unassigned:
                        self._publish_put_away_display()
                    else:
                        # All items assigned — switch to pantry view
                        self._publish_pantry_display()
                    loc = next((l for l in self._pantry["locations"] if l["id"] == location_id), None)
                    logger.info(f"[Pantry] Put-away: '{item['name']}' → {loc['name'] if loc else location_id}")

        elif action == "show_put_away":
            self._publish_put_away_display()

    # -----------------------------------------------------------------------
    # Tick handler — recurring items + expiry warnings
    # -----------------------------------------------------------------------

    def _on_tick(self, event: EventMessage):
        """Periodic check for recurring shopping items, expiry warnings, and mode timeout."""
        now = datetime.now()

        # Auto-exit shopping mode on inactivity timeout
        if self._shopping_mode and self._shopping_mode_last_activity:
            elapsed = (now - self._shopping_mode_last_activity).total_seconds()
            if elapsed > self._shopping_mode_timeout:
                logger.info(f"[Pantry] Shopping mode timed out after {elapsed:.0f}s of inactivity")
                self.event_system.publish(EventMessage(
                    "status", "shopping_mode", {"mode": None}
                ))
                self.event_system.publish(EventMessage(
                    "tts", "speak",
                    f"Shopping {self._shopping_mode.replace('_', ' ')} mode timed out."
                ))
                self._shopping_mode = None
                self._shopping_mode_last_activity = None

        # Check recurring items once per hour
        if self._last_recurring_check is None or (now - self._last_recurring_check).seconds >= 3600:
            self._last_recurring_check = now
            self._check_recurring()

        # Proactive expiry warning once per day — only fire when system is idle
        # (60s after startup, not while in shopping mode, not while speaking)
        today = date.today()
        if not self._expiry_warned_today or self._expiry_warned_today != today:
            startup_elapsed = (now - self._startup_time).total_seconds()
            if (startup_elapsed > 60 and not self._shopping_mode
                    and not self._tts_active):
                self._check_proactive_expiry(today)

    def _check_recurring(self):
        """Re-add recurring items to shopping list when due."""
        today = date.today()
        for rule in self._shopping_list.get("recurring_rules", []):
            try:
                next_due = date.fromisoformat(rule["next_due"])
            except (ValueError, KeyError):
                continue

            if next_due <= today:
                # Check if already on the list
                existing = self._find_shopping_item(rule["item_name"])
                if not existing:
                    self._shopping_list["items"].append({
                        "id": uuid.uuid4().hex[:8],
                        "name": rule["item_name"],
                        "quantity": rule.get("quantity"),
                        "category": _categorize_item(rule["item_name"]),
                        "added": datetime.now().isoformat(timespec="seconds"),
                        "got": False,
                    })
                    logger.info(f"[Pantry] Recurring: re-added '{rule['item_name']}' to shopping list")

                # Bump next_due forward
                rule["next_due"] = (today + timedelta(days=rule["interval_days"])).isoformat()

        self._save_shopping_list()

    _EXPIRY_MAX_PER_BUCKET = 3

    def _check_proactive_expiry(self, today: date):
        """Warn about items expiring within 2 days via TTS.

        Groups items by urgency bucket and phrases each bucket once, so a
        well-stocked fridge doesn't turn into a half-minute monologue. Each
        bucket is capped at ``_EXPIRY_MAX_PER_BUCKET`` items, with a
        "plus N more" tail if there are extras.
        """
        self._expiry_warned_today = today

        expired = []           # manual expiry, already past: (name, days_ago)
        expires_today = []     # manual, due today: names
        expires_tomorrow = []  # manual, due tomorrow: names
        expires_soon = []      # manual, 2 days out: names
        past_best = []         # estimated, already past: names
        getting_old = []       # estimated, today/tomorrow/2 days: names

        for item in self._pantry["items"]:
            if not item.get("expires"):
                continue
            try:
                exp = date.fromisoformat(item["expires"])
            except ValueError:
                continue
            days_left = (exp - today).days
            if days_left > 2:
                continue
            name = item["name"]
            is_estimated = item.get("expiry_source") == "estimated"
            if is_estimated:
                if days_left < 0:
                    past_best.append(name)
                else:
                    getting_old.append(name)
            elif days_left < 0:
                expired.append((name, -days_left))
            elif days_left == 0:
                expires_today.append(name)
            elif days_left == 1:
                expires_tomorrow.append(name)
            else:  # days_left == 2
                expires_soon.append(name)

        if not (expired or expires_today or expires_tomorrow
                or expires_soon or past_best or getting_old):
            return

        sentences = self._format_expiry_sentences(
            expired, expires_today, expires_tomorrow,
            expires_soon, past_best, getting_old,
        )
        warning = "Heads up. " + " ".join(sentences)
        self.event_system.publish(EventMessage("tts", "speak", warning))
        logger.info(f"[Pantry] Expiry warning: {warning}")

    @staticmethod
    def _format_expiry_sentences(expired, expires_today, expires_tomorrow,
                                  expires_soon, past_best, getting_old):
        """Turn urgency buckets into a list of short TTS-friendly sentences."""
        cap = PantryPlugin._EXPIRY_MAX_PER_BUCKET

        def trim(items):
            if len(items) <= cap:
                return items, 0
            return items[:cap], len(items) - cap

        def oxford(items):
            items = list(items)
            if not items:
                return ""
            if len(items) == 1:
                return items[0]
            if len(items) == 2:
                return f"{items[0]} and {items[1]}"
            return ", ".join(items[:-1]) + f", and {items[-1]}"

        def tail(n):
            return f", plus {n} more" if n else ""

        sentences = []

        if expired:
            shown, more = trim(expired)
            phrases = [
                f"{name} {d} day{'s' if d != 1 else ''} ago"
                for name, d in shown
            ]
            sentences.append(f"Already expired: {oxford(phrases)}{tail(more)}.")

        if expires_today:
            shown, more = trim(expires_today)
            sentences.append(f"Expiring today: {oxford(shown)}{tail(more)}.")

        if expires_tomorrow:
            shown, more = trim(expires_tomorrow)
            sentences.append(f"Expiring tomorrow: {oxford(shown)}{tail(more)}.")

        if expires_soon:
            shown, more = trim(expires_soon)
            sentences.append(f"Expiring in 2 days: {oxford(shown)}{tail(more)}.")

        if past_best:
            shown, more = trim(past_best)
            sentences.append(f"Probably past their best: {oxford(shown)}{tail(more)}.")

        if getting_old:
            shown, more = trim(getting_old)
            sentences.append(f"Might be getting old: {oxford(shown)}{tail(more)}.")

        return sentences
