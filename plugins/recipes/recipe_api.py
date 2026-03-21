import ast
import json
import os
import pickle
import re
from typing import List, Dict, Any

import spacy
from loguru import logger
from rapidfuzz import fuzz
from tqdm import tqdm

from glados.context.activity import Activity
from glados.system.event_system import EventSystem, EventMessage
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType

# Load the spaCy language model
nlp = spacy.load("en_core_web_sm")

from glados.system.plugin import PluginSystem

event_system = EventSystem()
plugin_manager = PluginSystem()
plugin_manager.register_system_prompt("When selecting a recipe, interpret the user's selection based on prior results "
                                      "and proceed without restarting the search.")

# Global variables to hold recipes and ingredients
data_file = "plugin_data/recipes/dataset.csv"
pickle_file = "plugin_data/recipes/cache.pkl"

# the actual recipes
recipes = []
# the ingredients cache is used for ingredient based searching
# and aligning the terms in the query to what exists in the dataset
ingredient_set = set()


def load_recipes(file_path: str):
    """
    Load and preprocess recipes from a CSV file.

    Args:
        file_path (str): Path to the CSV file.

    Returns:
        List[Dict]: A list of preprocessed recipes.
    """
    import pandas as pd

    global recipes, ingredient_set

    # Check if the pickle cache exists
    if os.path.exists(pickle_file):
        logger.info("Loading recipes from pickle cache...")
        with open(pickle_file, "rb") as f:
            recipes, ingredient_set = pickle.load(f)
        logger.success(f"Loaded {len(recipes)} recipes from cache.")
        event_system.publish(EventMessage(
            "tool",
            "plugin_system",
            f"The recip_api has loaded {len(recipes)} recipes from cache.",
            process_output=False
        ))
    else:
        logger.info("Processing recipes from CSV file...")
        df = pd.read_csv(file_path)
        recipes = []
        for _, row in tqdm(df.iterrows(), total=len(df), desc="Processing Recipes", unit="recipe"):
            ingredients = json.loads(row['NER'])
            cleaned_ingredients = clean_ingredients(ingredients)
            ingredient_set.update(cleaned_ingredients)

            try:
                recipes.append({
                    "title": row['title'].strip(),
                    "ingredients": row['ingredients'],
                    "directions": json.loads(row['directions'])
                })
            except:
                pass

        with open(pickle_file, "wb") as f:
            pickle.dump((recipes, ingredient_set), f)

    logger.success(f"Loaded {len(recipes)} recipes with {len(ingredient_set)} unique ingredients.")
    logger.success(f"Ingredients: {ingredient_set}")


def clean_ingredients(raw_ingredients: List[str]) -> List[str]:
    """
    Cleans and normalizes ingredient names.

    Args:
        raw_ingredients (List[str]): List of raw ingredient strings.

    Returns:
        List[str]: Cleaned ingredient names.
    """
    cleaned = []
    for ingredient in raw_ingredients:
        # Normalize ingredient strings
        ingredient = re.sub(r"\(.*?\)|\d+[\w\s\/\.]*", "", ingredient).strip()
        ingredient = re.sub(r"\s+", " ", ingredient).lower()
        if ingredient:
            cleaned.append(ingredient)
    return cleaned


def find_best_recipe(query: str) -> Any | None:
    """
    Find the best matching recipe for a given query using fuzzy matching.

    Args:
        query (str): The search query.

    Returns:
        Dict: The best matching recipe with its similarity score.
    """
    best_score = -1
    best_recipe = None

    for recipe in recipes:
        score = fuzz.ratio(query.lower(), recipe['title'].lower())
        if score > best_score:
            best_score = score
            best_recipe = recipe

    if best_recipe:
        best_recipe['similarity'] = round((best_score / 100), 2)
        return best_recipe

    return None


def search_by_ingredients(query_ingredients: List[str]) -> List[Dict]:
    """
    Search recipes by matching ingredients.

    Args:
        query_ingredients (List[str]): List of ingredients to match.

    Returns:
        List[Dict]: List of matching recipes sorted by relevance.
    """
    matching_recipes = []
    logger.success(f"Searching recipes that have ingredients {query_ingredients}")

    for recipe in recipes:
        common = set(query_ingredients).intersection(recipe['ingredients'])
        if common:
            matching_recipes.append({
                "title": recipe['title'],
                "ingredients": recipe['ingredients'],
                "directions": recipe['directions'],
                "matched_ingredients": list(common),
                "match_count": len(common)
            })

    return sorted(matching_recipes, key=lambda x: x['match_count'], reverse=True)


def convert_fractions(text: str) -> str:
    """Converts fractions and mixed numbers to TTS-friendly spoken forms.

    Handles: 1/2, 1/4, 3/4, 1/3, 2/3, 1/8, 3/8, mixed like '1 1/2',
    and arbitrary fractions like '5/6'.
    """
    fraction_map = {
        "1/2": "one half",
        "1/3": "one third",
        "2/3": "two thirds",
        "1/4": "one quarter",
        "3/4": "three quarters",
        "1/8": "one eighth",
        "3/8": "three eighths",
        "5/8": "five eighths",
        "7/8": "seven eighths",
    }

    # Mixed numbers first: "1 1/2" -> "one and a half"
    def replace_mixed(match):
        whole = match.group(1)
        frac = match.group(2)
        spoken_frac = fraction_map.get(frac)
        if spoken_frac:
            return f"{whole} and {spoken_frac}"
        # Fallback for unknown fractions
        num, den = frac.split("/")
        return f"{whole} and {num} over {den}"

    text = re.sub(r"(\d+)\s+(\d+/\d+)", replace_mixed, text)

    # Standalone fractions: "1/2" -> "one half"
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
        "tsp": "teaspoon",
        "tsps": "teaspoons",
        "tbl": "tablespoon",
        "tbls": "tablespoons",
        "tbsp": "tablespoon",
        "tbsps": "tablespoons",
        "oz": "ounce",
        "ozs": "ounces",
        "c": "cup",
        "qt": "quart",
        "qts": "quarts",
        "pt": "pint",
        "pts": "pints",
        "pkg": "package",
        "pkgs": "packages",
        "lb": "pound",
        "lbs": "pounds",
        "gal": "gallon",
        "lg": "large",
        "sm": "small",
        "med": "medium",
    }
    words = text.split()
    return " ".join([abbreviation_map.get(word.lower(), word) for word in words])


def format_ingredient_for_speech(ingredient: str) -> str:
    """Applies fraction conversion then abbreviation expansion to an ingredient string."""
    return convert_abbreviations(convert_fractions(ingredient))


def safe_parse_list(serialized_list: Any) -> list:
    """Safely parses a serialized list string into a Python list."""
    if isinstance(serialized_list, list):
        # If already a list, return it as-is
        return serialized_list
    try:
        # Try parsing if it is a string representation of a list
        return ast.literal_eval(serialized_list)
    except (ValueError, SyntaxError):
        logger.error(f"Failed to parse ingredients/directions: {serialized_list}")
        return []


CURRENT_RECIPES = []


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Search for recipes based on a query and returns a list of recipes.",
                                             parameters=Parameters(type="object", required=['query'], properties={
                                                 'query': ParameterType(type="string",
                                                                        description="the recipe name to search for")
                                             })
                                         )
                                         ),
    intents=[
        "find me a recipe for bread",
        "search recipes for chili con carne",
        "what recipes for pizza do you know"
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def search_recipes(query: str) -> dict:
    """
    Search for recipes matching the query.

    Args:
        query (str): The search query.

    Returns:
        str: List of matching recipes.
    """

    # query = extract_relevant_terms_nlp(query)
    logger.success(f"query for recipes: {query}")
    global CURRENT_RECIPES

    matches = []
    for recipe in recipes:
        score = fuzz.partial_ratio(query.lower(), recipe['title'].lower())
        if score > 50:
            matches.append((score, recipe))

    matches = sorted(matches, key=lambda x: x[0], reverse=True)

    if not matches:
        logger.warning(f"No recipes found for: {query}")
        return "No recipes found matching the enquiry was found in the recipe search API."

    # CURRENT_RECIPES = matches[:10]  # Store top matches globally

    result_data = []
    for score, recipe in matches[:10]:  # Limit to top 10 matches
        try:
            parsed_ingredients = safe_parse_list(recipe["ingredients"])
            ingredients_list = ", ".join(format_ingredient_for_speech(ing) for ing in parsed_ingredients)

            structured_recipe = (
                f"Title: {recipe['title']}, score: {score}, Ingredients: {ingredients_list}\n\n"
            )

            logger.info(f"append recipe: {structured_recipe}")
            result_data.append(structured_recipe)
        except Exception as e:
            logger.warning(f"Skipping recipe: {recipe}, cause: {e}")
    return (f"The following recipes were found by the recipe search API. Filter out all recipes "
            f"that are unrelated to the query '{query}', choose which best matches the query, "
            f"and ask the user to say 'select recipe' followed by the name of the recipe. "
            f"recipes: {result_data}")


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description=(
                                                     "Selects a recipe from the recipe database based on the name of the recipe, returning the "
                                                     "best match"
                                             ),
                                             parameters=Parameters(type="object", required=['query'], properties={
                                                 'query': ParameterType(type="string",
                                                                        description="the recipe by name from the recipe database")
                                             })
                                         )
                                         ),
    intents=[
        "lets make apple pie",
        "select a recipe for banana bread",
        "I want to make american pancakes"
    ],
    process_output=True,
    activity=[Activity.COOKING, Activity.GENERAL]
)
def select_recipe(query: str) -> dict:
    """
    Handles user selection of a recipe. Returns the recipe formatted for
    voice interaction with TTS-friendly ingredients and numbered steps.
    """

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
        parsed_directions = safe_parse_list(best_match["directions"])
        directions_section = "\n".join(
            f"Step {i + 1}: {format_ingredient_for_speech(step)}"
            for i, step in enumerate(parsed_directions)
        )

        # Auto-display recipe on connected screen (iPad)
        # Use raw ingredient strings with fractions since they're readable on screen
        display_ingredients = safe_parse_list(best_match["ingredients"])
        display_directions = safe_parse_list(best_match["directions"])
        event_system.publish(EventMessage(
            role="display",
            name="recipe",
            content={
                "title": best_match["title"],
                "ingredients": display_ingredients,
                "directions": display_directions,
            },
            process_output=False
        ))

        return {
            "status": "success",
            "title": best_match["title"],
            "ingredients": ingredients_section,
            "directions": directions_section,
            "message": (
                f"Selected recipe: {best_match['title']}. "
                f"Start by reading the ingredients to the user. "
                f"The user can ask for all ingredients, one at a time, "
                f"to repeat ingredients, or to move on to the cooking steps. "
                f"When giving cooking steps, give only one step at a time and "
                f"wait for the user to say they are ready for the next step."
            ),
        }

    except Exception as e:
        return {
            "status": "error",
            "message": "An unexpected error occurred while selecting the recipe.",
            "error": str(e),
        }


def find_recipe_by_ingredients(query: str) -> str:
    """
    Find recipes by ingredients.

    Args:
        query (str): Comma-separated list of ingredients.

    Returns:
        str: Matching recipes.
    """
    query_ingredients = [
        word.strip().lower()
        for word in re.split(r'[,\s]+', query)
        if word.strip().lower() in ingredient_set
    ]
    matches = search_by_ingredients(query_ingredients)

    if not matches:
        return ("DATA_RESPONSE:: The recipe API responded with no matching recipes found. Tell the user that no recipe "
                "was found, and cancel this line of inquiry. Make the response short, and to the point in this case.")

    response = ("DATA_RESPONSE:: The recipe API responded with the following recipes, compare them to the original "
                "request, and then present the most likely matches present, use the similarity score internally only. "
                "Dont read the similarity score to the user, and then let the user choose a recipe to continue with. "
                ":\n")
    for match in matches[:5]:
        response += f"- {match['title']} ({match['match_count']} matches)\n"
    return response


def extract_relevant_terms_nlp(query: str) -> str:
    """
    Extracts the main search term from a query using spaCy NLP.

    Args:
        query (str): The full search query.

    Returns:
        str: The extracted relevant terms.
    """
    doc = nlp(query)
    # Extract nouns and proper nouns as relevant terms
    relevant_terms = [token.text for token in doc if token.pos_ in ("NOUN", "PROPN")]
    return " ".join(relevant_terms)


# Load recipes at initialization
load_recipes(data_file)
