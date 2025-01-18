import json
import os
import pickle
import re
from typing import List, Dict, Any

from loguru import logger
from rapidfuzz import fuzz
from tqdm import tqdm
import spacy

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParamaterType

# Load the spaCy language model
nlp = spacy.load("en_core_web_sm")

from plugins.plugin_manager import PluginManager

plugin_manager = PluginManager()

# Global variables to hold recipes and ingredients
data_file = "plugin_data/recipes/test_dataset.csv"
pickle_file = "plugin_data/recipes/test_cache.pkl"

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
        best_recipe['similarity'] = best_score / 100
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



search_recipes_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='search_recipes',
                        description="Search for recipes",
                        parameters=Parameters(type="object", required=['query'], properties={
                            'query': ParamaterType(type="string", description="the recipe name to search for")
                        })
                    )
                    )
)



@plugin_manager.register(
    "search_recipes",
    "Search for recipes based on a query",
    function_request=search_recipes_definition.to_dict()
)
def search_recipes(query: str) -> str:
    """
    Search for recipes matching the query.

    Args:
        query (str): The search query.

    Returns:
        str: List of matching recipes.
    """

    query = extract_relevant_terms_nlp(query)
    logger.success(f"query shortened: {query}")

    matches = []
    for recipe in recipes:
        score = fuzz.partial_ratio(query.lower(), recipe['title'].lower())
        if score > 50:
            matches.append((score, recipe))

    matches = sorted(matches, key=lambda x: x[0], reverse=True)

    if not matches:
        return "No recipe could be found"

#     response = """
# The recipe API responded with multiple recipes. Compare the following recipes to the original request,
# and ask for confirmation in selecting the single most likely recipe by title alone that matches the requested recipe.
# Do not reply with details or ingredients until after the selection is confirmed.
#
# For the selected recipe, process the data as follows:
#
# 1. When generating response, you must conver all abreviated units to their unabbreviate forms before presenting them. Use the
# following mappings:
#    - tsp -> teaspoon
#    - Tbsp -> tablespoon
#    - c -> cup
#    - oz -> ounce
#    - lb -> pound
#    - g -> gram
#    - kg -> kilogram
#    - ml -> milliliter
#    - l -> liter
#
# 2. You must Convert fractions to their textual representation:
#    - 1/2 -> half
#    - 1/4 -> quarter
#    - 3/4 -> three-quarters
#    - 1/3 -> one-third
#    - 2/3 -> two-thirds
#    - three/four -> three-quarters
#    - Example: "1 1/2 c." -> "one and a half cups"
#
# 3. you must Provide the ingredient list with fully unabbreviated quantities and units in the following format:
#    - "1 tsp. salt" -> "one teaspoon salt"
#    - "2 c. milk" -> "two cups milk"
#    - "3/4 c. sugar" -> "three-quarters cup sugar"
#
# 4. Generate step-by-step preparation instructions. Clarify each step to make it actionable. Example:
#    - "Mix dry ingredients" -> "In a large mixing bowl, combine the flour, sugar, salt, and baking powder."
#
# 5. Pause after each step and prompt for confirmation before proceeding to the next step. Allow the user to restart or
# stop the instructions at any time.
#
# 6. If no recipe matches the query, terminate this response and indicate that no suitable recipe was found.
#
# Use the following recipes as input data:
#
# """
    response = ("Choose the most suitable recipes based on the original request and ask which one to proceed with, once "
                "confirmed present the recipe as step by step instructions, pausing between ingredients and directions, "
                "and asking for confirmation to continue between each step of the process")
    for score, recipe in matches[:10]:  # Limit to top 10 matches
        response += f" recipe_data: _title:{recipe['title']}, _similarity:{score}%, _ingredients:{recipe['ingredients']}, _directions:{recipe['directions']})\n"
    return response


@plugin_manager.register(
    "find_recipe_by_ingredients",
    "Search for recipes based on a ingredients at hand",
    function_request={
        "query": {"type": "str", "description": "Comma separated list of ingredients to to search recipes for"}
    },
)
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
        return "DATA_RESPONSE:: The recipe API responded with no matching recipes found. Tell the user that no recipe was found, and cancel this line of inquiry. Make the response short, and to the point in this case."

    response = "DATA_RESPONSE:: The recipe API responded with the following recipes, compare them to the original request, and then present the most likely matches present, use the similarity score internally only. Dont read the similarity score to the user, and then let the user choose a recipe to continue with. :\n"
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
