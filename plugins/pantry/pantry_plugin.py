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


# ---------------------------------------------------------------------------
# Category auto-assignment
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "dairy": ["milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "eggs", "egg"],
    "produce": [
        "apple", "banana", "lettuce", "tomato", "onion", "potato", "carrot",
        "pepper", "cucumber", "spinach", "broccoli", "avocado", "lemon", "lime",
        "garlic", "ginger", "mushroom", "celery", "corn", "peas", "beans",
    ],
    "meat": [
        "chicken", "beef", "pork", "bacon", "sausage", "mince", "steak", "lamb",
        "turkey", "ham", "salami", "prosciutto", "duck",
    ],
    "seafood": ["fish", "salmon", "tuna", "prawns", "shrimp", "cod", "haddock"],
    "frozen": ["ice cream", "frozen peas", "fish fingers", "pizza"],
    "bakery": ["bread", "rolls", "croissant", "bagel", "muffin", "wrap", "tortilla", "pitta"],
    "dry_goods": ["rice", "pasta", "flour", "sugar", "cereal", "oats", "lentils", "couscous", "noodles"],
    "beverages": ["juice", "water", "soda", "coffee", "tea", "beer", "wine"],
    "condiments": ["ketchup", "mustard", "mayo", "mayonnaise", "sauce", "oil", "vinegar", "salt", "pepper"],
    "snacks": ["crisps", "chips", "nuts", "chocolate", "biscuits", "cookies", "popcorn"],
}


def _categorize_item(name: str) -> str:
    """Auto-assign a shopping category based on item name."""
    name_lower = name.lower()
    for category, keywords in CATEGORY_MAP.items():
        for keyword in keywords:
            if keyword in name_lower or fuzz.partial_ratio(keyword, name_lower) >= 85:
                return category
    return "other"


# Ready meal keywords — items matching these are classified as complete dishes
READY_MEAL_KEYWORDS = [
    "lasagna", "lasagne", "pizza", "pie", "soup", "stew", "curry", "casserole",
    "quiche", "pasta bake", "mac and cheese", "shepherd's pie", "cottage pie",
    "ready meal", "tv dinner", "microwave meal", "frozen dinner", "frozen meal",
    "leftover", "leftovers", "cooked", "prepared", "meal prep",
    "burrito", "wrap", "sandwich", "salad", "risotto", "paella",
    "chili", "chilli", "bolognese", "moussaka", "enchilada", "frittata",
    "tikka", "korma", "biryani", "stir fry", "stir-fry", "fried rice",
    "roast", "gratin", "ratatouille", "goulash", "tagine",
    "dumplings", "gyoza", "samosa", "empanada", "spring rolls",
    "fish cake", "fishcake", "fish fingers", "nuggets", "schnitzel",
    "meatballs", "meatloaf", "pot pie", "calzone", "focaccia",
]


def _classify_item_type(name: str) -> str:
    """Classify a pantry item as 'ready_meal' or 'ingredient'.

    Uses substring matching only (no fuzzy) to avoid false positives
    like 'rice' matching 'risotto'. Multi-word keywords use token overlap.
    """
    name_lower = name.lower()
    for keyword in READY_MEAL_KEYWORDS:
        if keyword in name_lower:
            return "ready_meal"
        # For multi-word items, check if all keyword words appear in the name
        kw_words = keyword.split()
        if len(kw_words) > 1 and all(w in name_lower for w in kw_words):
            return "ready_meal"
    return "ingredient"


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
# NLP extract/response functions (module-level, per countdown_timer pattern)
# ---------------------------------------------------------------------------

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}


def _add_to_list_nlp_extract(text: str) -> dict:
    """Extract item name, optional quantity, and recurring days from natural language."""
    params = {}
    text_lower = text.lower().strip()

    # Extract recurring: "every N days/weeks" (digits or word numbers)
    recurring_match = re.search(r"every\s+(\w+)\s+(day|week)s?", text_lower)
    if recurring_match:
        raw_n = recurring_match.group(1)
        unit = recurring_match.group(2)
        n = _WORD_NUMBERS.get(raw_n)
        if n is None:
            try:
                n = int(raw_n)
            except ValueError:
                n = None
        if n is not None:
            params["recurring_days"] = n * 7 if unit == "week" else n
            text_lower = text_lower[:recurring_match.start()].strip()

    # Strip common prefixes to get item name
    cleaned = re.sub(
        r"^(?:please\s+)?(?:add|put|we\s+(?:buy|need|get)|we\'re\s+out\s+of|we\s+are\s+out\s+of|"
        r"i\s+need|we\s+need\s+to\s+(?:buy|get)|running\s+low\s+on|"
        r"buy|get\s+some|get)\s+",
        "", text_lower, flags=re.IGNORECASE
    ).strip()

    # Strip common suffixes
    cleaned = re.sub(
        r"\s+(?:to|on|from)\s+(?:the\s+)?(?:shopping\s+)?list\s*$",
        "", cleaned, flags=re.IGNORECASE
    ).strip()

    if cleaned:
        params["item"] = cleaned
    return params


def _add_to_list_nlp_response(result: dict) -> str:
    if result.get("status") == "error":
        return result.get("message", "Could not add that to the list.")
    item = result.get("item", "item")
    if result.get("recurring_days"):
        return f"Added {item} to the shopping list, recurring every {result['recurring_days']} days."
    return f"Added {item} to the shopping list."


def _remove_from_list_nlp_extract(text: str) -> dict:
    cleaned = re.sub(
        r"^(?:please\s+)?(?:remove|take\s+off|delete|cross\s+off)\s+(?:the\s+)?",
        "", text.lower().strip(), flags=re.IGNORECASE
    ).strip()
    cleaned = re.sub(
        r"\s+(?:from|off)\s+(?:the\s+)?(?:shopping\s+)?list\s*$",
        "", cleaned, flags=re.IGNORECASE
    ).strip()
    return {"item": cleaned} if cleaned else {}


def _remove_from_list_nlp_response(result: dict) -> str:
    if result.get("status") == "success":
        return f"Removed {result.get('item', 'that')} from the shopping list."
    return result.get("message", "That item isn't on the list.")


def _show_list_nlp_response(result: dict) -> str:
    count = result.get("count", 0)
    if count == 0:
        return "The shopping list is empty."
    return f"You have {count} item{'s' if count != 1 else ''} on the shopping list. I've put it on the screen."


def _complete_shopping_nlp_extract(text: str) -> dict:
    """Extract 'except' items from 'we got everything except X, Y and Z'."""
    m = re.search(r"except\s+(.+)", text.lower())
    if m:
        raw = m.group(1).strip().rstrip(".")
        # Split on commas and "and"
        items = re.split(r"\s*(?:,\s*(?:and\s+)?|(?:\s+and\s+))", raw)
        items = [i.strip() for i in items if i.strip()]
        return {"except_items": ", ".join(items)}
    return {}


def _complete_shopping_nlp_response(result: dict) -> str:
    moved = result.get("moved", 0)
    remaining = result.get("remaining", 0)
    if moved == 0 and remaining == 0:
        return "The shopping list was already empty."
    msg = f"Done. Moved {moved} item{'s' if moved != 1 else ''} to the pantry."
    if remaining > 0:
        items = result.get("remaining_items", [])
        if items:
            msg += f" {', '.join(items)} remain{'s' if len(items) == 1 else ''} on the list."
    return msg


def _store_item_nlp_extract(text: str) -> dict:
    """Extract item and location from 'I put X in Y'. Supports 'as a meal' type override."""
    # Check for explicit type: "as a meal" / "as a ready meal" / "as an ingredient"
    type_match = re.search(r"\s+as\s+(?:an?\s+)?(ready\s+meal|meal|ingredient)", text, re.IGNORECASE)
    cleaned_text = re.sub(r"\s+as\s+(?:an?\s+)?(?:ready\s+meal|meal|ingredient)", "", text, flags=re.IGNORECASE)

    m = re.search(
        r"(?:put|stored|placed|the)\s+(?:the\s+)?(.+?)\s+(?:in|into|on)\s+(?:the\s+)?(.+)",
        cleaned_text.lower().strip()
    )
    if m:
        item = m.group(1).strip()
        location = m.group(2).strip().rstrip(".")
        params = {"item": item, "location": location}
        # Check for expiry in remaining text
        expiry_m = re.search(r"expires?\s+(?:on\s+)?(.+)", location)
        if expiry_m:
            params["location"] = location[:expiry_m.start()].strip()
            params["expires"] = expiry_m.group(1).strip()
        # Apply type override
        if type_match:
            params["item_type"] = "ready_meal" if "meal" in type_match.group(1).lower() else "ingredient"
        return params
    return {}


def _store_item_nlp_response(result: dict) -> str:
    if result.get("status") == "success":
        return f"Noted. {result.get('item', 'Item')} is in {result.get('location', 'the pantry')}."
    return result.get("message", "Could not store that.")


def _set_expiry_nlp_extract(text: str) -> dict:
    """Extract item and expiry date from 'X expires on Y' or 'X best before Y'."""
    m = re.search(
        r"(?:the\s+)?(.+?)\s+(?:expires?|best\s+before|use\s+by)\s+(?:on\s+)?(.+)",
        text.lower().strip()
    )
    if m:
        return {"item": m.group(1).strip(), "expires": m.group(2).strip().rstrip(".")}
    return {}


def _set_expiry_nlp_response(result: dict) -> str:
    if result.get("status") == "success":
        return f"Got it. {result.get('item', 'Item')} expires {result.get('expires', '')}."
    return result.get("message", "Could not set expiry.")


def _find_item_nlp_extract(text: str) -> dict:
    cleaned = re.sub(
        r"^(?:where\s+(?:is|are|did\s+I\s+put)\s+(?:the\s+)?|"
        r"do\s+we\s+have\s+(?:any\s+)?|is\s+there\s+(?:any\s+)?|"
        r"have\s+we\s+got\s+(?:any\s+)?|check\s+(?:if\s+we\s+have\s+)?|"
        r"find\s+(?:the\s+)?)",
        "", text.lower().strip(), flags=re.IGNORECASE
    ).strip().rstrip("?.")
    return {"item": cleaned} if cleaned else {}


def _find_item_nlp_response(result: dict) -> str:
    if result.get("status") == "found":
        matches = result.get("matches", [])
        if len(matches) == 1:
            m = matches[0]
            msg = f"{m['name']} is in {m['location']}"
            if m.get("expires"):
                msg += f", expires {m['expires']}"
            return msg + "."
        parts = [f"{m['name']} in {m['location']}" for m in matches]
        return "Found: " + ", ".join(parts) + "."
    if result.get("status") == "not_found":
        item = result.get("item", "that")
        on_list = result.get("on_shopping_list", False)
        if on_list:
            return f"I don't see {item} in the pantry, but it's on the shopping list."
        return f"I don't see {item} in the pantry."
    return result.get("message", "Not found.")


def _check_expiring_nlp_extract(text: str) -> dict:
    m = re.search(r"(\d+)\s+days?", text.lower())
    if m:
        return {"days": int(m.group(1))}
    if "this week" in text.lower():
        return {"days": 7}
    if "tomorrow" in text.lower():
        return {"days": 1}
    return {}


def _check_expiring_nlp_response(result: dict) -> str:
    items = result.get("expiring", [])
    if not items:
        return "Nothing is expiring soon."
    parts = []
    for item in items:
        parts.append(f"{item['name']} in {item['location']} (expires {item['expires']})")
    return "Expiring soon: " + ". ".join(parts) + "."


def _show_pantry_nlp_extract(text: str) -> dict:
    """Extract optional location filter from 'what's in the fridge'."""
    m = re.search(
        r"(?:what(?:'s|\s+is)\s+in\s+(?:the\s+)?|show\s+(?:me\s+)?(?:the\s+)?)(.+?)(?:\s*\?)?$",
        text.lower().strip()
    )
    if m:
        loc = m.group(1).strip()
        if loc not in ("pantry", "kitchen", "inventory", "storage"):
            return {"location": loc}
    return {}


def _show_pantry_nlp_response(result: dict) -> str:
    count = result.get("total_items", 0)
    if count == 0:
        return "The pantry is empty."
    location = result.get("location_filter")
    if location:
        return f"{count} item{'s' if count != 1 else ''} in {location}. I've put it on the screen."
    return f"{count} item{'s' if count != 1 else ''} in the pantry. I've put it on the screen."


def _suggest_meals_nlp_response(result: dict) -> str:
    if result.get("status") != "success":
        return result.get("message", "Couldn't find any recipe suggestions.")
    return result.get("message", "I've put some suggestions on the screen.")


def _add_recipe_ingredients_nlp_extract(text: str) -> dict:
    # "add the ingredients for X to the shopping list" or just use last selected recipe
    m = re.search(r"(?:ingredients?\s+(?:for|from)\s+)(.+?)(?:\s+to\s+(?:the\s+)?(?:shopping|list))?$",
                  text, re.IGNORECASE)
    return {"recipe_name": m.group(1).strip() if m else ""}


def _add_recipe_ingredients_nlp_response(result: dict) -> str:
    if result.get("status") != "success":
        return result.get("message", "Couldn't add recipe ingredients.")
    added = result.get("added", 0)
    skipped = result.get("skipped", 0)
    parts = []
    if added:
        parts.append(f"Added {added} ingredient{'s' if added != 1 else ''} to the shopping list")
    if skipped:
        parts.append(f"skipped {skipped} we already have")
    return ". ".join(parts) + "." if parts else "Done."


def _check_recipe_ingredients_nlp_response(result: dict) -> str:
    if result.get("status") != "success":
        return result.get("message", "Couldn't check ingredients.")
    have = result.get("have", [])
    missing = result.get("missing", [])
    if not missing:
        return f"You have everything you need. All {len(have)} ingredients are in the pantry."
    return f"You're missing {len(missing)} ingredient{'s' if len(missing) != 1 else ''}: {', '.join(missing[:5])}."


def _manage_locations_nlp_extract(text: str) -> dict:
    text_lower = text.lower()
    if "rename" in text_lower:
        m = re.search(r"rename\s+(?:the\s+)?(.+?)\s+to\s+(.+?)\.?$", text, re.IGNORECASE)
        if m:
            return {"action": "rename", "name": m.group(1).strip(), "new_name": m.group(2).strip()}
    elif "remove" in text_lower or "delete" in text_lower:
        m = re.search(r"(?:remove|delete)\s+(?:the\s+)?(?:location\s+)?(?:called\s+)?(.+?)\.?$",
                      text, re.IGNORECASE)
        if m:
            return {"action": "remove", "name": m.group(1).strip()}
    elif "add" in text_lower:
        m = re.search(r"add\s+(?:a\s+)?(?:location\s+)?(?:called\s+)?(.+?)\.?$", text, re.IGNORECASE)
        if m:
            return {"action": "add", "name": m.group(1).strip()}
    return {}


def _manage_locations_nlp_response(result: dict) -> str:
    return result.get("message", "Done.")


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class PantryPlugin(RunnableMCPPlugin):
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
                {"id": "fridge", "name": "Fridge"},
                {"id": "freezer-1", "name": "Freezer Drawer 1"},
                {"id": "freezer-2", "name": "Freezer Drawer 2"},
                {"id": "freezer-3", "name": "Freezer Drawer 3"},
                {"id": "dry-goods", "name": "Dry Goods Cupboard"},
            ],
            "items": [],
        })

        self._last_recurring_check = None
        self._expiry_warned_today = False
        self._startup_time = datetime.now()
        self._shopping_mode = None  # None, "planning", or "post_shopping"
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
        self.register_view("shopping_list", "plugins/pantry/views/shopping.js", dashboard_card=True)
        self.register_view("pantry", "plugins/pantry/views/pantry.js", dashboard_card=True)

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
    # Fuzzy matching helpers
    # -----------------------------------------------------------------------

    def _find_shopping_item(self, name: str) -> Optional[dict]:
        """Find a shopping list item by fuzzy name match."""
        name_lower = name.lower()
        best, best_score = None, 0
        for item in self._shopping_list["items"]:
            score = fuzz.ratio(name_lower, item["name"].lower())
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= 70 else None

    def _find_pantry_items(self, name: str) -> list[dict]:
        """Find pantry items by fuzzy name match. Returns all matches above threshold."""
        name_lower = name.lower()
        matches = []
        for item in self._pantry["items"]:
            score = fuzz.partial_ratio(name_lower, item["name"].lower())
            if score >= 70:
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
            ],
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

    def _register_recipe_integration_tools(self):
        self.register_tool(
            handler=self.suggest_meals_from_pantry,
            description=(
                "Suggest meals or recipes based on what's currently in the pantry. "
                "Prioritizes ingredients that are expiring soon."
            ),
            parameters={
                "use_expiring_first": {
                    "type": "boolean",
                    "description": "If true (default), prioritize expiring ingredients",
                },
            },
            required=[],
            intents=[
                "what can I make with what's in the pantry",
                "what can I make with the contents of the pantry",
                "what can I make with whats in the pantry",
                "what can I cook with what we have",
                "what can I cook with the pantry contents",
                "what can I make before things expire",
                "suggest a meal from the pantry",
                "suggest a meal from what we have",
                "suggest meals from pantry",
                "recipe suggestions from pantry",
                "recipe suggestions from what we have",
                "suggest a meal",
                "what meals can I make",
                "what recipes can I make with what I have",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_response=_suggest_meals_nlp_response,
        )

        self.register_tool(
            handler=self.add_recipe_ingredients_to_list,
            description=(
                "Add ingredients from a recipe to the shopping list. "
                "Cross-references with pantry to skip items we already have."
            ),
            parameters={
                "recipe_name": {
                    "type": "string",
                    "description": "Recipe name to look up ingredients for",
                },
            },
            required=["recipe_name"],
            intents=[
                "add the ingredients for that to the shopping list",
                "add recipe ingredients to the list",
                "what do I need to buy for this recipe",
                "shopping list for this recipe",
            ],
            process_output=True,
            activity=[Activity.COOKING],
            nlp_extract_fn=_add_recipe_ingredients_nlp_extract,
            nlp_response=_add_recipe_ingredients_nlp_response,
        )

        self.register_tool(
            handler=self.check_recipe_ingredients,
            description=(
                "Check which ingredients for a recipe we already have in the pantry, "
                "and which ones we're missing. Use when the user asks 'do we have the "
                "ingredients' or 'what do I need to buy for this recipe'."
            ),
            parameters={
                "recipe_name": {
                    "type": "string",
                    "description": "Recipe name to check. If empty, uses the last selected recipe.",
                },
            },
            required=[],
            intents=[
                "do we have the ingredients",
                "do we have these ingredients",
                "do we have the ingredients for this",
                "what ingredients are we missing",
                "can we make this recipe",
                "check if we have the ingredients",
                "do I need to buy anything for this recipe",
            ],
            process_output=True,
            activity=[Activity.COOKING, Activity.GENERAL],
            nlp_response=_check_recipe_ingredients_nlp_response,
        )

    # -----------------------------------------------------------------------
    # Recipe integration tools
    # -----------------------------------------------------------------------

    def check_recipe_ingredients(self, recipe_name: str = None) -> dict:
        """Check which recipe ingredients we have vs. what's missing."""
        try:
            from plugins.recipes.recipe_api import select_recipe, safe_parse_list, _last_search_results, _last_selected_recipe
        except ImportError:
            return {"status": "error", "message": "Recipe plugin is not available."}

        # If no recipe specified, use the last selected or first search result
        if not recipe_name:
            if _last_selected_recipe:
                recipe_name = _last_selected_recipe["title"]
            elif _last_search_results:
                recipe_name = _last_search_results[0]
        if not recipe_name:
            return {"status": "error", "message": "No recipe specified. Search for a recipe first."}

        # Use cached data if it matches, otherwise select fresh
        recipe_data = None
        if _last_selected_recipe and _last_selected_recipe.get("title", "").lower() == recipe_name.lower():
            recipe_data = _last_selected_recipe
        else:
            result = select_recipe(recipe_name)
            if result.get("status") != "success":
                return result
            from plugins.recipes.recipe_api import _last_selected_recipe as fresh
            recipe_data = fresh

        if not recipe_data:
            return {"status": "error", "message": "Could not load recipe data."}

        raw_ingredients = recipe_data.get("ingredients", "")
        ingredient_lines = [
            line.strip().lstrip("- ").strip()
            for line in raw_ingredients.split("\n")
            if line.strip()
        ]

        have = []
        missing = []
        for ing in ingredient_lines:
            if self._find_pantry_items(ing):
                have.append(ing)
            else:
                missing.append(ing)

        title = recipe_data.get("title", recipe_name)
        return {
            "status": "success",
            "recipe": title,
            "have": have,
            "have_count": len(have),
            "missing": missing,
            "missing_count": len(missing),
            "total": len(ingredient_lines),
            "message": (
                f"For {title}: you have {len(have)} of {len(ingredient_lines)} ingredients. "
                + (f"Missing: {', '.join(missing[:5])}." if missing else "You have everything!")
            ),
        }

    def suggest_meals_from_pantry(self, use_expiring_first: bool = True) -> dict:
        """Suggest recipes based on pantry contents, prioritizing expiring items."""
        if not self._pantry["items"]:
            return {"status": "empty", "message": "The pantry is empty. Nothing to suggest."}

        # Separate ready meals from ingredients
        ready_meals = []
        ingredient_items = []
        for item in self._pantry["items"]:
            if item.get("item_type") == "ready_meal":
                loc = next((l for l in self._pantry["locations"] if l["id"] == item.get("location_id")), None)
                ready_meals.append({
                    "name": item["name"],
                    "location": loc["name"] if loc else "unknown",
                    "expires": item.get("expires"),
                })
            else:
                ingredient_items.append(item)

        # Prioritize expiring ingredients
        expiring = []
        if use_expiring_first:
            today = date.today()
            others = []
            for item in ingredient_items:
                if item.get("expires"):
                    try:
                        exp = date.fromisoformat(item["expires"])
                        if (exp - today).days <= 7:
                            expiring.append(item["name"])
                            continue
                    except ValueError:
                        pass
                others.append(item["name"])
            query_items = expiring + others
        else:
            query_items = [i["name"] for i in ingredient_items]

        # Use the recipe plugin's ingredient search
        try:
            from plugins.recipes.recipe_api import search_by_ingredients, safe_parse_list
            import plugins.recipes.recipe_api as _recipe_mod
            matches = search_by_ingredients(query_items[:15]) if query_items else []
            logger.info(f"[Pantry] Recipe suggestion: {len(matches)} matches from {len(query_items)} ingredients, {len(ready_meals)} ready meals")

            top = matches[:10]

            # Push results to display as recipe search view
            display_results = [
                {
                    "title": m["title"],
                    "image_name": m.get("image_name"),
                    "ingredient_count": len(m.get("ingredients", [])),
                }
                for m in top
            ]
            self.event_system.publish(EventMessage(
                role="display", name="recipe_search",
                content={
                    "title": "Recipes from Pantry",
                    "query": "pantry ingredients",
                    "results": display_results,
                    "ready_meals": ready_meals,
                },
                process_output=False,
            ))

            # Populate shared search results for positional selection ("the first one")
            _recipe_mod._last_search_results = [m["title"] for m in top]

            # Build message with ready meals first, then recipe suggestions
            parts = []
            if ready_meals:
                meal_names = ", ".join(m["name"] for m in ready_meals[:5])
                parts.append(f"Ready to eat: {meal_names}")
            if top:
                titles = [m["title"] for m in top[:5]]
                parts.append(f"Recipes you can make: {', '.join(titles)}")
            if not parts:
                return {"status": "no_results", "message": "No ready meals or recipe suggestions found."}

            message = ". ".join(parts) + ". I've put them on the screen."
            if top:
                message += " Say the first one, the second one, or the recipe name to select."

            return {
                "status": "success",
                "count": len(top),
                "ready_meals": ready_meals,
                "top_recipes": [m["title"] for m in top[:5]],
                "message": message,
            }
        except ImportError:
            return {"status": "error", "message": "Recipe plugin is not available."}
        except Exception as e:
            logger.warning(f"[Pantry] Recipe suggestion failed: {e}")
            return {"status": "error", "message": f"Could not search recipes: {e}"}

    def add_recipe_ingredients_to_list(self, recipe_name: str) -> dict:
        """Add ingredients from a recipe to the shopping list, skipping what we already have."""
        try:
            from plugins.recipes.recipe_api import select_recipe, _last_selected_recipe
        except ImportError:
            return {"status": "error", "message": "Recipe plugin is not available."}

        # Use cached recipe data if available and matches, otherwise select fresh
        recipe_data = None
        if _last_selected_recipe and _last_selected_recipe.get("title", "").lower() == recipe_name.lower():
            recipe_data = _last_selected_recipe
        else:
            result = select_recipe(recipe_name)
            if result.get("status") != "success":
                return result
            # select_recipe stores full data in _last_selected_recipe
            from plugins.recipes.recipe_api import _last_selected_recipe as fresh
            recipe_data = fresh

        if not recipe_data:
            return {"status": "error", "message": "Could not load recipe data."}

        # Parse ingredients from the recipe
        raw_ingredients = recipe_data.get("ingredients", "")
        ingredient_lines = [
            line.strip().lstrip("- ").strip()
            for line in raw_ingredients.split("\n")
            if line.strip()
        ]

        from glados.nlp.ingredient_parser import parse_ingredient_list
        parsed = parse_ingredient_list(ingredient_lines)

        added = []
        skipped = []
        for ing in parsed:
            item_name = ing["item"]
            quantity = ing["quantity"]
            # Check if we already have it in the pantry
            if self._find_pantry_items(item_name):
                skipped.append(item_name)
                continue
            # Check if already on shopping list
            if self._find_shopping_item(item_name):
                skipped.append(item_name)
                continue
            # Add to shopping list with parsed name and quantity
            self.add_to_shopping_list(item=item_name, quantity=quantity)
            added.append(item_name)

        self._publish_shopping_list_display()
        logger.info(f"[Pantry] Added {len(added)} recipe ingredients, skipped {len(skipped)}")
        return {
            "status": "success",
            "recipe": result.get("title", recipe_name),
            "added": len(added),
            "added_items": added,
            "skipped": len(skipped),
            "skipped_items": skipped,
            "message": f"Added {len(added)} ingredients for {result.get('title', recipe_name)}. "
                       f"Skipped {len(skipped)} items you already have.",
        }

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

    def add_to_shopping_list(self, item: str, quantity: str = None, recurring_days: int = None) -> dict:
        """Add an item to the shopping list."""
        # Check if already on the list
        existing = self._find_shopping_item(item)
        if existing:
            if quantity:
                existing["quantity"] = quantity
                self._save_shopping_list()
            return {"status": "exists", "item": existing["name"],
                    "message": f"{existing['name']} is already on the shopping list."}

        new_item = {
            "id": uuid.uuid4().hex[:8],
            "name": item,
            "quantity": quantity,
            "category": _categorize_item(item),
            "added": datetime.now().isoformat(timespec="seconds"),
            "got": False,
        }
        self._shopping_list["items"].append(new_item)

        # Set up recurring rule if requested
        if recurring_days and recurring_days > 0:
            # Remove existing rule for this item
            self._shopping_list["recurring_rules"] = [
                r for r in self._shopping_list["recurring_rules"]
                if r["item_name"].lower() != item.lower()
            ]
            self._shopping_list["recurring_rules"].append({
                "item_name": item,
                "quantity": quantity,
                "interval_days": recurring_days,
                "next_due": (date.today() + timedelta(days=recurring_days)).isoformat(),
            })

        # If item is in pantry, remove it (they said they're out)
        pantry_matches = self._find_pantry_items(item)
        if pantry_matches:
            for pm in pantry_matches:
                self._pantry["items"].remove(pm)
            self._save_pantry()
            logger.info(f"[Pantry] Removed {len(pantry_matches)} pantry entries for '{item}' (user is out)")

        self._save_shopping_list()
        self._publish_shopping_list_display()
        logger.info(f"[Pantry] Added '{item}' to shopping list (category: {new_item['category']})")

        return {
            "status": "added", "item": item, "category": new_item["category"],
            "recurring_days": recurring_days,
            "count": len(self._shopping_list["items"]),
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
        """Complete shopping — move bought items to pantry."""
        except_names = []
        if except_items:
            except_names = [n.strip().lower() for n in re.split(r"\s*,\s*", except_items)]

        moved = []
        remaining = []

        for item in list(self._shopping_list["items"]):
            is_excepted = any(
                fuzz.ratio(item["name"].lower(), exc) >= 70 for exc in except_names
            )
            if is_excepted:
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
                    "notes": item.get("quantity"),
                }
                self._pantry["items"].append(pantry_item)
                moved.append(item)

        # Update shopping list to only keep excepted items
        self._shopping_list["items"] = remaining
        self._save_shopping_list()
        self._save_pantry()
        self._publish_shopping_list_display()

        logger.info(f"[Pantry] Shopping complete — moved {len(moved)}, {len(remaining)} remaining")
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
        if expires:
            expires_date = _parse_expiry_date(expires)

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
            existing["stored"] = datetime.now().isoformat(timespec="seconds")
            existing["item_type"] = item_type or existing.get("item_type") or effective_type
        else:
            self._pantry["items"].append({
                "id": uuid.uuid4().hex[:8],
                "name": item,
                "location_id": loc["id"],
                "stored": datetime.now().isoformat(timespec="seconds"),
                "expires": expires_date,
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

        updated = 0
        for item in self._pantry["items"]:
            old_type = item.get("item_type")
            if use_llm:
                new_type = self._llm_classify_item(client, model, item["name"])
            else:
                new_type = _classify_item_type(item["name"])
            if new_type and new_type != old_type:
                item["item_type"] = new_type
                updated += 1
                logger.info(f"[Pantry] Reclassified '{item['name']}': {old_type} -> {new_type}")

        if updated:
            self._save_pantry()
            self._publish_pantry_display()

        method = "LLM" if use_llm else "keyword"
        total = len(self._pantry["items"])
        logger.info(f"[Pantry] Reclassification complete: {updated}/{total} changed ({method})")
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
            self._pantry["locations"].append({"id": loc_id, "name": name})
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
                "items": [
                    {
                        "id": i["id"],
                        "name": i["name"],
                        "notes": i.get("notes"),
                        "expires": i.get("expires"),
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
                "items": [
                    {
                        "id": i["id"],
                        "name": i["name"],
                        "notes": i.get("notes"),
                        "expires": i.get("expires"),
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
    # Shopping sub-context (planning / post-shopping modes)
    # -----------------------------------------------------------------------

    _PLANNING_TRIGGERS = [
        "lets plan the shopping", "lets plan shopping", "shopping planning mode",
        "plan the shopping list", "lets make a shopping list", "plan shopping",
        "planning mode", "start planning", "lets plan", "shopping mode",
    ]
    _POST_SHOPPING_TRIGGERS = [
        "we're back from shopping", "back from the shops", "back from shopping",
        "post shopping", "lets put away the shopping", "unpack the shopping",
        "post shopping mode", "back from the store",
    ]
    _EXIT_TRIGGERS = [
        "done", "that's everything", "finished", "exit", "done planning",
        "stop planning", "all done", "that's it", "that's all", "exit mode",
        "leave mode", "stop", "end planning mode", "end planning",
        "exit planning mode", "exit planning", "end mode",
        "cancel", "cancelled", "cancel that", "never mind", "nevermind",
        "stop it", "quit", "close", "end", "that is cancelled",
    ]

    # Tool names allowed in each mode (for LLM tool_override)
    _PLANNING_TOOLS = [
        "add_to_shopping_list", "remove_from_shopping_list", "show_shopping_list",
        "suggest_meals_from_pantry", "find_item", "check_expiring",
    ]
    _POST_SHOPPING_TOOLS = [
        "complete_shopping", "store_item", "set_expiry", "find_item",
        "show_pantry", "show_shopping_list",
    ]

    @staticmethod
    def _clean_voice_text(text: str) -> str:
        """Clean Whisper transcription artifacts for trigger matching."""
        # Strip whitespace, trailing punctuation, lowercase
        return re.sub(r"[.!?,;]+$", "", text.strip().lower()).strip()

    _SHOPPING_MODE_TIMEOUT_DEFAULT = 60  # seconds of inactivity before auto-exiting mode

    def _shopping_context_hook(self, ctx):
        """PRE_LLM hook: intercept commands when in a shopping sub-context."""
        text = self._clean_voice_text(ctx.user_text)

        # --- Check for mode entry ---
        if not self._shopping_mode:
            if any(text.startswith(t) or text == t for t in self._PLANNING_TRIGGERS):
                self._shopping_mode = "planning"
                self._shopping_mode_last_activity = datetime.now()
                self._publish_shopping_list_display()
                self.event_system.publish(EventMessage(
                    "status", "shopping_mode", {"mode": "planning"}
                ))
                ctx.tts_queue.put("Shopping planning mode. Just say the item name to add it, or remove followed by the item.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                logger.info("[Pantry] Entered planning mode")
                return

            if any(fuzz.ratio(text, t) >= 80 or text.startswith(t) for t in self._POST_SHOPPING_TRIGGERS):
                self._shopping_mode = "post_shopping"
                self._shopping_mode_last_activity = datetime.now()
                self._publish_shopping_list_display()
                self.event_system.publish(EventMessage(
                    "status", "shopping_mode", {"mode": "post_shopping"}
                ))
                ctx.tts_queue.put("Post-shopping mode. Tell me what you got and where you put things. Say done when finished.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                logger.info("[Pantry] Entered post-shopping mode")
                return

            return  # Not in a mode, let normal processing handle it

        # --- Check for mode exit ---
        if any(text == t or text.startswith(t) or fuzz.ratio(text, t) >= 80 for t in self._EXIT_TRIGGERS):
            if self._shopping_mode == "post_shopping":
                result = self.complete_shopping()
                moved = result.get("moved", 0)
                remaining = result.get("remaining", 0)
                ctx.tts_queue.put(f"Done. Moved {moved} items to the pantry. {remaining} items remain on the list.")
            else:
                count = len(self._shopping_list["items"])
                ctx.tts_queue.put(f"Done planning. You have {count} items on the shopping list.")
            ctx.tts_queue.put("<EOS>")
            self.event_system.publish(EventMessage(
                "status", "shopping_mode", {"mode": None}
            ))
            logger.info(f"[Pantry] Exited {self._shopping_mode} mode")
            self._shopping_mode = None
            ctx.handled = True
            return

        # --- Handle commands within the active mode ---
        handled = False
        if self._shopping_mode == "planning":
            handled = self._handle_planning_command(text, ctx)
        elif self._shopping_mode == "post_shopping":
            handled = self._handle_post_shopping_command(text, ctx)

        if handled:
            self._shopping_mode_last_activity = datetime.now()

        if not handled:
            # Command not recognized by short-form handlers — pass through to LLM
            # but with restricted tool set
            tool_names = self._PLANNING_TOOLS if self._shopping_mode == "planning" else self._POST_SHOPPING_TOOLS
            mode_label = "shopping planning" if self._shopping_mode == "planning" else "post-shopping"

            # Build the filtered tool list for the LLM
            from glados.system.plugin import PluginSystem
            from glados.llm.client_type import ClientType
            all_tools = PluginSystem().get_available_tools(architecture=ClientType.OPENAI)
            filtered = [t for t in all_tools if isinstance(t, dict) and
                        t.get("function", {}).get("name") in tool_names]
            ctx.extra["tool_override"] = filtered

            # Inject mode context
            mode_prompt = (
                f"You are in {mode_label} mode. Only use the available shopping/pantry tools. "
                f"Help the user manage their shopping list and pantry."
            )
            if ctx.memory_context:
                ctx.memory_context = mode_prompt + "\n\n" + ctx.memory_context
            else:
                ctx.memory_context = mode_prompt

    def _handle_planning_command(self, text: str, ctx) -> bool:
        """Handle short commands in planning mode. Returns True if handled."""
        # "remove X"
        if text.startswith("remove ") or text.startswith("delete "):
            item = re.sub(r"^(?:remove|delete)\s+(?:the\s+)?", "", text).strip()
            if item:
                result = self.remove_from_shopping_list(item)
                msg = _remove_from_list_nlp_response(result)
                ctx.tts_queue.put(msg)
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        # "make that 6" / "made that six bananas" / "6 of those" / "actually 3"
        qty_match = re.match(
            r"(?:ma[dk]e\s+(?:that|it)\s+|actually\s+)(\w+)(?:\s+.*)?$"
            r"|(\w+)\s+of\s+(?:those|them)",
            text
        )
        if qty_match and self._last_added_item:
            raw_qty = (qty_match.group(1) or qty_match.group(2) or "").strip()
            from glados.nlp.extractors import word_to_number
            num = word_to_number(raw_qty)
            if num is not None or raw_qty.isdigit():
                qty = str(num) if num is not None else raw_qty
                self._last_added_item["quantity"] = qty
                self._save_shopping_list()
                self._publish_shopping_list_display()
                ctx.tts_queue.put(f"Updated to {qty} {self._last_added_item['name']}.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        # "show the list" / "list"
        if text in ("show the list", "list", "show list", "what's on the list"):
            self.show_shopping_list()
            ctx.tts_queue.put(f"You have {len(self._shopping_list['items'])} items on the list.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        # Default: treat as "add X" — bare item names
        # But first, reject text that's clearly not a shopping item:
        # - Too long (>6 words is probably a sentence, not an item)
        # - Contains verbs/pronouns that indicate conversation, not items
        words = text.split()
        non_item_patterns = re.compile(
            r"\b(i think|i want|why|how|what|when|where|because|that's why|"
            r"this is|they|my|your|we need to|can you|could you|please help|"
            r"it's|cancelled|cancel|beep|gladys|glados)\b", re.IGNORECASE
        )
        if len(words) > 6 or non_item_patterns.search(text):
            # Doesn't look like a shopping item — pass to LLM with restricted tools
            return False

        # Strip leading "add" / "and" / "also"
        item_text = re.sub(r"^(?:add|and|also|plus)\s+(?:some\s+)?", "", text).strip()
        if item_text and len(item_text) > 1:
            # Split "X and Y" or "X, Y and Z" into separate items
            items = re.split(r"\s*(?:,\s*(?:and\s+)?|\s+and\s+)\s*", item_text)
            items = [i.strip() for i in items if i.strip() and len(i.strip()) > 1]
            if not items:
                return False
            added_names = []
            for item in items:
                # Strip leading "a/an/some"
                item = re.sub(r"^(?:a|an|some)\s+", "", item).strip()
                if item:
                    result = self.add_to_shopping_list(item)
                    self._last_added_item = next(
                        (i for i in self._shopping_list["items"] if i["name"].lower() == item.lower()), None
                    )
                    added_names.append(item)
            if added_names:
                listing = ", ".join(added_names)
                ctx.tts_queue.put(f"Added {listing} to the shopping list.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        return False

    def _handle_post_shopping_command(self, text: str, ctx) -> bool:
        """Handle short commands in post-shopping mode. Returns True if handled."""
        # "got X" / "got the X" / "yes" (marks last mentioned)
        got_match = re.match(r"(?:got|got\s+the|we\s+got|yes)\s*(.*)", text)
        if got_match:
            item_name = got_match.group(1).strip()
            if item_name:
                match = self._find_shopping_item(item_name)
                if match:
                    match["got"] = True
                    self._save_shopping_list()
                    self._publish_shopping_list_display()
                    ctx.tts_queue.put(f"Marked {match['name']} as got.")
                    ctx.tts_queue.put("<EOS>")
                    ctx.handled = True
                    return True

        # "didn't get X" / "no X" / "not the X" / "skip X"
        skip_match = re.match(r"(?:didn't\s+get|no|not\s+the|skip|not)\s+(.*)", text)
        if skip_match:
            item_name = skip_match.group(1).strip()
            if item_name:
                match = self._find_shopping_item(item_name)
                if match:
                    match["got"] = False
                    self._save_shopping_list()
                    self._publish_shopping_list_display()
                    ctx.tts_queue.put(f"{match['name']} stays on the list.")
                    ctx.tts_queue.put("<EOS>")
                    ctx.handled = True
                    return True

        # "put X in Y" — delegate to store_item
        store_match = re.search(r"(?:put|stored|placed)\s+(?:the\s+)?(.+?)\s+(?:in|into)\s+(?:the\s+)?(.+)", text)
        if store_match:
            item = store_match.group(1).strip()
            location = store_match.group(2).strip()
            result = self.store_item(item, location)
            ctx.tts_queue.put(_store_item_nlp_response(result))
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        # "X expires Y" — delegate to set_expiry
        expiry_match = re.search(r"(.+?)\s+(?:expires?|best\s+before|use\s+by)\s+(?:on\s+)?(.+)", text)
        if expiry_match:
            item = expiry_match.group(1).strip().lstrip("the ")
            expires = expiry_match.group(2).strip()
            result = self.set_expiry(item, expires)
            ctx.tts_queue.put(_set_expiry_nlp_response(result))
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        return False  # Not handled — pass to LLM with restricted tools

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
                self._pantry["items"].append({
                    "id": uuid.uuid4().hex[:8],
                    "name": name,
                    "location_id": location_id,
                    "stored": datetime.now().isoformat(timespec="seconds"),
                    "expires": expires,
                    "notes": notes,
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
                    logger.info(f"[Pantry] UI: set expiry for '{item['name']}' to {expires}")
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

    def _check_proactive_expiry(self, today: date):
        """Warn about items expiring within 2 days via TTS."""
        self._expiry_warned_today = today
        expiring = []
        for item in self._pantry["items"]:
            if item.get("expires"):
                try:
                    exp = date.fromisoformat(item["expires"])
                    days_left = (exp - today).days
                    if days_left <= 2:
                        loc_name = self._location_name(item["location_id"])
                        if days_left < 0:
                            expiring.append(f"the {item['name']} in the {loc_name} expired {-days_left} days ago")
                        elif days_left == 0:
                            expiring.append(f"the {item['name']} in the {loc_name} expires today")
                        elif days_left == 1:
                            expiring.append(f"the {item['name']} in the {loc_name} expires tomorrow")
                        else:
                            expiring.append(f"the {item['name']} in the {loc_name} expires in {days_left} days")
                except ValueError:
                    continue

        if expiring:
            warning = "Heads up: " + ", and ".join(expiring) + "."
            self.event_system.publish(EventMessage("tts", "speak", warning))
            logger.info(f"[Pantry] Expiry warning: {warning}")
