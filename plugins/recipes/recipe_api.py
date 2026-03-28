import ast
import os
import pickle
import re
from collections import defaultdict
from typing import List, Dict, Any

from loguru import logger
from rapidfuzz import fuzz

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
pickle_file = "plugin_data/recipes/cache_v2.pkl"

recipes = []
ingredient_set = set()
# Reverse index: ingredient word → set of recipe indices (for fast ingredient search)
ingredient_index = defaultdict(set)


def load_recipes(file_path: str):
    """Load and preprocess recipes from a CSV file."""
    global recipes, ingredient_set, ingredient_index

    if os.path.exists(pickle_file):
        logger.info("Loading recipes from pickle cache...")
        with open(pickle_file, "rb") as f:
            data = pickle.load(f)
        recipes = data["recipes"]
        ingredient_set = data["ingredient_set"]
        ingredient_index = data.get("ingredient_index", defaultdict(set))
        logger.success(f"Loaded {len(recipes)} recipes from cache.")
        event_system.publish(EventMessage(
            "tool", "plugin_system",
            f"The recipe system has loaded {len(recipes)} recipes from cache.",
            process_output=False
        ))
    else:
        if not os.path.exists(file_path):
            logger.warning(f"Recipe dataset not found at {file_path}")
            return
        logger.info(f"Processing recipes from CSV file: {file_path}")
        _load_from_csv(file_path)
        # Save cache
        os.makedirs(os.path.dirname(pickle_file), exist_ok=True)
        with open(pickle_file, "wb") as f:
            pickle.dump({
                "recipes": recipes,
                "ingredient_set": ingredient_set,
                "ingredient_index": dict(ingredient_index),
            }, f)
        logger.success(f"Saved recipe cache to {pickle_file}")

    # Rebuild index if not in cache
    if not ingredient_index and recipes:
        _build_ingredient_index()

    logger.success(f"Loaded {len(recipes)} recipes with {len(ingredient_set)} unique ingredients.")


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

                # Image name (maps to data/recipes/img/Food Images/{name}.jpg)
                image_name = row.get("Image_Name", "").strip() or None

                recipe_idx = len(recipes)
                recipes.append({
                    "title": title,
                    "ingredients": raw_ingredients,
                    "directions": directions,
                    "cleaned_ingredients": cleaned,
                    "image_name": image_name,
                })

                # Build ingredient word index
                for ing in cleaned:
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
        for ing in recipe.get("cleaned_ingredients", []):
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
    """Search recipes by fuzzy ingredient matching. Uses the word index for speed."""
    if not query_ingredients:
        return []

    # Find candidate recipes via word index
    candidate_indices = set()
    for q_ing in query_ingredients:
        for word in q_ing.lower().split():
            if word in ingredient_index:
                candidate_indices.update(ingredient_index[word])

    # Fuzzy match only against candidates
    matching_recipes = []
    for idx in candidate_indices:
        recipe = recipes[idx]
        recipe_ings = recipe.get("cleaned_ingredients", [])
        matched = []
        for q_ing in query_ingredients:
            for r_ing in recipe_ings:
                if fuzz.partial_ratio(q_ing.lower(), r_ing) >= 75:
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


def format_ingredient_for_speech(ingredient: str) -> str:
    """Applies fraction conversion then abbreviation expansion to an ingredient string."""
    return convert_abbreviations(convert_fractions(ingredient))


def safe_parse_list(serialized_list: Any) -> list:
    """Safely parses a serialized list string into a Python list."""
    if isinstance(serialized_list, list):
        return serialized_list
    try:
        return ast.literal_eval(serialized_list)
    except (ValueError, SyntaxError):
        logger.debug(f"Failed to parse list: {str(serialized_list)[:80]}")
        return []


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
        "what can I cook with chicken",
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

    result_data = []
    display_results = []
    for score, recipe in matches[:10]:
        try:
            parsed_ingredients = safe_parse_list(recipe["ingredients"])
            ingredients_list = ", ".join(format_ingredient_for_speech(ing) for ing in parsed_ingredients[:8])

            structured_recipe = (
                f"Title: {recipe['title']}, score: {score}, Ingredients: {ingredients_list}\n\n"
            )
            result_data.append(structured_recipe)
            display_results.append({
                "title": recipe["title"],
                "image_name": recipe.get("image_name"),
                "ingredient_count": len(parsed_ingredients),
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
    ],
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


def find_recipe_by_ingredients(query: str) -> str:
    """Find recipes by ingredients. Used by pantry plugin for meal suggestions."""
    query_ingredients = [
        word.strip().lower()
        for word in re.split(r'[,\s]+', query)
        if word.strip() and len(word.strip()) > 2
    ]
    if not query_ingredients:
        return "No valid ingredients provided."

    matches = search_by_ingredients(query_ingredients)

    if not matches:
        return ("DATA_RESPONSE:: The recipe API responded with no matching recipes found. Tell the user that no recipe "
                "was found, and cancel this line of inquiry. Make the response short, and to the point in this case.")

    response = ("DATA_RESPONSE:: The recipe API responded with the following recipes, compare them to the original "
                "request, and then present the most likely matches present, use the similarity score internally only. "
                "Dont read the similarity score to the user, and then let the user choose a recipe to continue with. "
                ":\n")
    for match in matches[:5]:
        matched = ", ".join(match.get("matched_ingredients", []))
        response += f"- {match['title']} (matched: {matched})\n"
    return response


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
            # No TTS — the user clicked it in the UI, they can see the result
    elif action == "search_from_pantry":
        # Call pantry plugin's suggest_meals_from_pantry directly — no TTS for UI actions
        try:
            from plugins.pantry.pantry_plugin import PantryPlugin
            pp = PantryPlugin()
            pp.suggest_meals_from_pantry()
        except Exception as e:
            logger.warning(f"[Recipe] Pantry search failed: {e}")


# Register the UI action with the plugin system
plugin_manager.register_ui_action("recipe_action", _on_recipe_action)

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
