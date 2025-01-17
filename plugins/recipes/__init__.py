import requests
from loguru import logger

from plugins.plugin_manager import PluginManager
from plugins.recipes.util import (load_and_preprocess_recipes, find_best_recipe, list_matching_recipes,
                                  extract_ingredients)

plugin_manager = PluginManager()

recipes, ingredient_set_raw = load_and_preprocess_recipes("plugin_data/recipes/recipes.json")
ingredient_set = set()


def pre_initialize_recipes(glados_instance) -> None:
    """
    Pre-initializes the recipes plugin by refining the ingredient set using the local LLM.

    Args:
        glados_instance (Glados): An instance of the Glados class to access the local model.
    """
    global ingredient_set_raw
    global ingredient_set
    logger.info("Pre-initializing recipes plugin...")
    ingredient_prompt = (
        "Extract the ingredient from the following string, extract only the ingredient, and respond with only the "
        "ingredients name.\n"
    )

    try:
        # Use the local model from Glados
        for raw_ingredient in ingredient_set_raw:
            try:
                refined_response = glados_instance.model.generate(
                    prompt=ingredient_prompt,
                    max_tokens=500,
                    temperature=0.7
                )

                # Assuming the model returns a string of refined ingredients
                refined_ingredients = refined_response.split(", ")
                ingredient_set.update(refined_ingredients)
                logger.success(f"Updated ingredient with: {refined_ingredients}")
            except Exception as e:
                logger.error(f"Failed to refine ingredients using the local LLM: {e}")
    except Exception as e2:
        logger.error(f"Failed to refine ingredients using the local LLM: {e2}")


plugin_manager.add_pre_init_hook(pre_initialize_recipes)


@plugin_manager.register("recipes")
def handle_recipe(inquiry: str) -> str:
    best_recipe = find_best_recipe(inquiry, recipes)
    logger.success(f"Found best recipe: {best_recipe['title']}")

    # Structured response
    response = (
        "DATA_FOLLOWS:: "
        "The following data is from a recipe API. "
        "First repeat the name of the recipe and ask for confirmation if the receip is indeed the desired one, "
        "Once the recipe is confirmed, Turn this into the requested recipe format, providing instructions step by step, "
        "waiting for confirmation after each step. "
        "If clarification or ingredient substitutions are needed, ask the user explicitly. "
        "All units should be converted to metric where possible. "
        "\n\n"
        f"Title: {best_recipe['title']}\n"
        f"Ingredients: {', '.join(best_recipe['ingredients'])}\n"
        f"Instructions: {best_recipe['instructions']}"
    )
    return response


@plugin_manager.register("list_recipes")
def list_recipes(inquiry: str) -> str:
    matching_recipes = list_matching_recipes(inquiry, recipes)
    if not matching_recipes:
        return "No matching recipes found."
    result = "DATA_FOLLOWS:: the following data is from a Recipe API, analyse the response which contains similarity score, and present this most likely items to the user\n"
    for recipe in matching_recipes:
        result += f" - {recipe['title']} (Similarity: {recipe['similarity']:.2f})\n"
    return result


@plugin_manager.register("find_recipe_by_ingredients")
def find_recipe_by_ingredients(inquiry: str) -> str:
    """
    Finds recipes based on user-provided ingredients.

    Args:
        inquiry (str): The user's input containing ingredients.

    Returns:
        str: A list of matching recipes or a message if no matches are found.
    """
    user_ingredients = extract_ingredients(inquiry, ingredient_set)
    logger.success(f"Searching for recipes that call for: {user_ingredients}")

    if not user_ingredients:
        return "I couldn't identify any ingredients in your input. Please try again with a list of ingredients."

    matching_recipes = []
    for recipe in recipes:
        recipe_ingredients = [i.lower() for i in recipe["ingredients"]]
        match_count = sum(1 for i in user_ingredients if any(i in ri for ri in recipe_ingredients))
        if match_count > 0:
            matching_recipes.append((recipe, match_count))

    matching_recipes.sort(key=lambda x: x[1], reverse=True)

    if not matching_recipes:
        return "No recipes found matching the provided ingredients."

    response = "DATA_FOLLOWS:: The Receip API has responded with recipes matching the ingredients, present this list to the user and let them choose which one they are interrested in making:\n"

    for recipe, match_count in matching_recipes[:5]:  # Limit to top 5 matches
        response += f"- {recipe['title']} (Matched Ingredients: {match_count})\n"
    return response
