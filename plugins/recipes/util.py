import json
import re
from typing import List, Dict

import numpy as np
from loguru import logger
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def load_and_preprocess_recipes(file_path: str):
    """
    Loads and preprocesses recipes from a JSON file.

    Args:
        file_path (str): Path to the JSON file.

    Returns:
        List[Dict]: A list of preprocessed recipes.
    """
    with open(file_path, 'r') as f:
        data = json.load(f)

    recipes = []
    ingredient_set = set()

    for recipe_id, recipe in data.items():
        # Validate required keys
        if 'title' not in recipe or 'ingredients' not in recipe or 'instructions' not in recipe:
            logger.warning(f"Skipping recipe ID {recipe_id}: Missing required fields.")
            continue

        raw_ingredients = recipe["ingredients"]
        cleaned_ingredients = clean_ingredients(raw_ingredients)
        ingredient_set.update(cleaned_ingredients)

        recipes.append({
            "id": recipe_id,
            "title": recipe["title"],
            "ingredients": [
                re.sub(r"ADVERTISEMENT", "", ingredient).strip()
                for ingredient in recipe["ingredients"]
                if ingredient.strip() != "ADVERTISEMENT"
            ],
            "instructions": re.sub(r"ADVERTISEMENT", "", recipe["instructions"]).strip(),
            "picture_link": recipe.get("picture_link", ""),
        })

    logger.success(f"{ingredient_set}")
    return recipes, ingredient_set


def clean_ingredients(raw_ingredients):
    """
    Cleans and extracts unique ingredient names from raw ingredient strings.

    Args:
        raw_ingredients (list): List of raw ingredient strings.

    Returns:
        set: A set of cleaned ingredient names.
    """
    cleaned_ingredients = set()
    for ingredient in raw_ingredients:
        # Remove unwanted text like "advertisement"
        ingredient = re.sub(r"\badvertisement\b", "", ingredient, flags=re.IGNORECASE).strip()
        # # Remove quantities and descriptors (e.g., "2 cups", "1/2-inch-thick pieces")
        # ingredient = re.sub(r"^\d+[^\w]*|\b\d+[-/]?\d*\s*\w*\b", "", ingredient).strip()
        # # Remove phrases in parentheses and extra descriptors
        # ingredient = re.sub(r"\(.*?\)|,.*|;.*", "", ingredient).strip()
        # # Lowercase and add to set
        if ingredient:
            cleaned_ingredients.add(ingredient.lower())
    return cleaned_ingredients


def find_best_recipe(inquiry: str, recipes: List[Dict]) -> Dict:
    titles = [recipe["title"] for recipe in recipes]
    ingredients = [" ".join(recipe["ingredients"]) for recipe in recipes]

    # Combine titles and ingredients for better matching
    texts = [f"{title} {ingr}" for title, ingr in zip(titles, ingredients)]

    # Create TF-IDF matrix
    vectorizer = TfidfVectorizer().fit_transform([inquiry] + texts)
    vectors = vectorizer.toarray()

    # Compute cosine similarity
    similarities = cosine_similarity(vectors[0:1], vectors[1:]).flatten()

    # Find the most similar recipe
    best_match_index = np.argmax(similarities)
    best_match = recipes[best_match_index]
    best_match["similarity"] = similarities[best_match_index]
    return best_match


def extract_ingredients(input_text: str, ingredient_set: set) -> list:
    """
    Extracts potential ingredients from user input using a pre-built ingredient database.

    Args:
        input_text (str): User's input containing potential ingredients.
        ingredient_set (set): Set of known ingredients.

    Returns:
        list: Extracted ingredients.
    """
    # Tokenize and normalize input text
    words = re.findall(r"\b\w+\b", input_text.lower())

    # Match words against the ingredient set
    extracted_ingredients = [word for word in words if word in ingredient_set]

    return extracted_ingredients


def list_matching_recipes(inquiry: str, recipes: List[Dict], threshold: float = 0.2) -> List[Dict]:
    titles = [recipe["title"] for recipe in recipes]
    ingredients = [" ".join(recipe["ingredients"]) for recipe in recipes]
    texts = [f"{title} {ingr}" for title, ingr in zip(titles, ingredients)]

    vectorizer = TfidfVectorizer().fit_transform([inquiry] + texts)
    vectors = vectorizer.toarray()

    similarities = cosine_similarity(vectors[0:1], vectors[1:]).flatten()

    # Return recipes that match above the threshold
    matching_recipes = [
        {**recipes[i], "similarity": sim}
        for i, sim in enumerate(similarities) if sim > threshold
    ]
    return sorted(matching_recipes, key=lambda x: x["similarity"], reverse=True)
