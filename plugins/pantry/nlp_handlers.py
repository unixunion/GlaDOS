"""NLP extract/response helpers for the pantry plugin.

Pure functions: each pair maps natural-language utterances to tool params
(`_*_nlp_extract`) and tool results back to spoken responses (`_*_nlp_response`).
Kept module-level (per the countdown_timer pattern) so they can be passed
directly to `register_tool(nlp_extract_fn=..., nlp_response=...)`.
"""

import re

from glados.nlp.extractors import _ONES as _WORD_NUMBERS

__all__ = [
    "_add_to_list_nlp_extract", "_add_to_list_nlp_response",
    "_remove_from_list_nlp_extract", "_remove_from_list_nlp_response",
    "_show_list_nlp_response",
    "_complete_shopping_nlp_extract", "_complete_shopping_nlp_response",
    "_store_item_nlp_extract", "_store_item_nlp_response",
    "_set_expiry_nlp_extract", "_set_expiry_nlp_response",
    "_find_item_nlp_extract", "_find_item_nlp_response",
    "_check_expiring_nlp_extract", "_check_expiring_nlp_response",
    "_show_pantry_nlp_extract", "_show_pantry_nlp_response",
    "_suggest_meals_nlp_response",
    "_add_recipe_ingredients_nlp_extract", "_add_recipe_ingredients_nlp_response",
    "_check_recipe_ingredients_nlp_response",
    "_manage_locations_nlp_extract", "_manage_locations_nlp_response",
]


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
        m = re.search(r"(?:remove|delete)\s+(?:the\s+)?(?:location\s+)?(.+?)\.?$",
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
