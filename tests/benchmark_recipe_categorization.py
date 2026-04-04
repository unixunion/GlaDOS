"""Benchmark LLM recipe categorization — tests accuracy across models.

Runs ground-truth recipe examples through each available LLM model
and compares against hand-labeled categories.

Usage:
    python tests/benchmark_recipe_categorization.py
    python tests/benchmark_recipe_categorization.py --url http://localhost:1234/v1
    python tests/benchmark_recipe_categorization.py --models model1 model2
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

# Ground truth: (title, ingredients, expected_meal_type, expected_cuisine)
GROUND_TRUTH = [
    # Clear Italian
    ("Classic Lasagna", ["pasta", "beef", "ricotta", "mozzarella", "tomato sauce"], "mains", "italian"),
    ("Spaghetti Carbonara", ["spaghetti", "pancetta", "eggs", "parmesan", "black pepper"], "mains", "italian"),
    ("Chicken Parmigiana", ["chicken breast", "mozzarella", "tomato sauce", "breadcrumbs"], "mains", "italian"),
    ("Tiramisu", ["mascarpone", "espresso", "ladyfingers", "cocoa"], "baking & desserts", "italian"),
    ("Risotto alla Milanese", ["arborio rice", "saffron", "parmesan", "butter", "onion"], "mains", "italian"),
    ("Italian Sausage and Peppers", ["italian sausage", "bell pepper", "onion", "garlic", "olive oil"], "mains", "italian"),

    # Clear Asian
    ("Pad Thai", ["rice noodles", "shrimp", "peanuts", "fish sauce", "bean sprouts"], "mains", "asian"),
    ("Chicken Teriyaki", ["chicken", "soy sauce", "mirin", "ginger", "rice"], "mains", "asian"),
    ("Beef Pho", ["rice noodles", "beef", "star anise", "cinnamon", "fish sauce"], "soups & stews", "asian"),
    ("Korean Bibimbap", ["rice", "beef", "gochujang", "spinach", "egg"], "mains", "asian"),
    ("Miso Soup", ["miso paste", "tofu", "seaweed", "scallions", "dashi"], "soups & stews", "asian"),
    ("Spring Rolls", ["rice paper", "shrimp", "lettuce", "vermicelli", "mint"], "appetizers & snacks", "asian"),

    # Clear Indian
    ("Chicken Tikka Masala", ["chicken", "yogurt", "garam masala", "tomatoes", "cream"], "mains", "indian"),
    ("Palak Paneer", ["paneer", "spinach", "cumin", "turmeric", "garam masala"], "mains", "indian"),
    ("Naan Bread", ["flour", "yogurt", "yeast", "garlic", "butter"], "bread", "indian"),
    ("Samosas", ["potatoes", "peas", "cumin", "coriander", "pastry"], "appetizers & snacks", "indian"),
    ("Dal Tadka", ["lentils", "turmeric", "cumin", "garlic", "tomatoes"], "mains", "indian"),

    # Clear Mexican & Latin
    ("Guacamole", ["avocado", "lime", "cilantro", "jalapeño", "onion"], "appetizers & snacks", "mexican & latin"),
    ("Chicken Enchiladas", ["chicken", "tortillas", "enchilada sauce", "cheese", "sour cream"], "mains", "mexican & latin"),
    ("Tacos al Pastor", ["pork", "pineapple", "achiote", "cilantro", "onion"], "mains", "mexican & latin"),
    ("Black Bean Soup", ["black beans", "cumin", "lime", "cilantro", "jalapeño"], "soups & stews", "mexican & latin"),
    ("Churros", ["flour", "sugar", "cinnamon", "butter", "eggs"], "baking & desserts", "mexican & latin"),

    # Clear Mediterranean
    ("Greek Salad", ["tomato", "cucumber", "feta", "olives", "olive oil"], "salads", "mediterranean"),
    ("Falafel", ["chickpeas", "cumin", "parsley", "garlic", "tahini"], "mains", "middle eastern"),
    ("Hummus", ["chickpeas", "tahini", "lemon", "garlic", "olive oil"], "appetizers & snacks", "middle eastern"),
    ("Tabbouleh", ["bulgur", "parsley", "tomato", "lemon", "olive oil"], "salads", "mediterranean"),
    ("Moussaka", ["eggplant", "lamb", "béchamel", "tomato", "cinnamon"], "mains", "mediterranean"),

    # Clear French
    ("French Onion Soup", ["onion", "gruyère", "beef broth", "bread", "thyme"], "soups & stews", "french"),
    ("Crème Brûlée", ["cream", "egg yolks", "vanilla", "sugar"], "baking & desserts", "french"),
    ("Coq au Vin", ["chicken", "red wine", "bacon", "mushrooms", "pearl onions"], "mains", "french"),
    ("Ratatouille", ["eggplant", "zucchini", "bell pepper", "tomato", "herbes de provence"], "sides", "french"),

    # Clear American
    ("Mac and Cheese", ["macaroni", "cheddar", "butter", "milk", "breadcrumbs"], "mains", "american"),
    ("Southern Fried Chicken", ["chicken", "buttermilk", "flour", "paprika", "cayenne"], "mains", "american"),
    ("New England Clam Chowder", ["clams", "potatoes", "cream", "bacon", "celery"], "soups & stews", "american"),
    ("BBQ Pulled Pork", ["pork shoulder", "bbq sauce", "coleslaw", "buns"], "mains", "american"),
    ("Banana Bread", ["banana", "flour", "sugar", "butter", "eggs"], "bread", "american"),

    # Clear British
    ("Fish and Chips", ["cod", "potatoes", "beer batter", "peas", "malt vinegar"], "mains", "british"),
    ("Shepherd's Pie", ["lamb", "potatoes", "carrots", "peas", "gravy"], "mains", "british"),
    ("Scones", ["flour", "butter", "cream", "sugar", "baking powder"], "baking & desserts", "british"),

    # Meal type edge cases
    ("Pancakes", ["flour", "eggs", "milk", "butter", "maple syrup"], "breakfast", "american"),
    ("Caesar Salad", ["romaine", "parmesan", "croutons", "anchovies", "lemon"], "salads", "american"),
    ("Tomato Basil Soup", ["tomatoes", "basil", "cream", "onion", "garlic"], "soups & stews", "other"),
    ("Margarita", ["tequila", "lime", "triple sec", "salt"], "drinks", "mexican & latin"),
    ("Chocolate Chip Cookies", ["butter", "sugar", "flour", "chocolate chips", "eggs"], "baking & desserts", "american"),
    ("Garlic Bread", ["bread", "butter", "garlic", "parsley"], "bread", "italian"),
    ("Potato Salad", ["potatoes", "mayonnaise", "mustard", "celery", "eggs"], "sides", "american"),
]

# Import categorizer
from tools.categorize_recipes import SYSTEM_PROMPT, VALID_MEALS, VALID_CUISINES, _parse_response


def classify_one(client, model, title, ingredients):
    """Call LLM to classify. Returns (meal, cuisine, latency_ms)."""
    user_msg = f"{title} | {', '.join(ingredients)}"
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=30,
        temperature=0,
    )
    latency = (time.perf_counter() - t0) * 1000
    raw = response.choices[0].message.content
    meal, cuisine = _parse_response(raw)
    return meal, cuisine, latency


def run_benchmark(url, models):
    from openai import OpenAI
    client = OpenAI(base_url=url, api_key="not-needed")

    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")

        meal_correct = 0
        cuisine_correct = 0
        both_correct = 0
        total = len(GROUND_TRUTH)
        total_latency = 0
        failures = []

        for title, ingredients, exp_meal, exp_cuisine in GROUND_TRUTH:
            try:
                meal, cuisine, latency = classify_one(client, model, title, ingredients)
                total_latency += latency

                meal_ok = meal == exp_meal
                cuisine_ok = cuisine == exp_cuisine
                if meal_ok:
                    meal_correct += 1
                if cuisine_ok:
                    cuisine_correct += 1
                if meal_ok and cuisine_ok:
                    both_correct += 1

                if not cuisine_ok:
                    failures.append((title, "cuisine", exp_cuisine, cuisine))
                if not meal_ok:
                    failures.append((title, "meal", exp_meal, meal))
            except Exception as e:
                failures.append((title, "ERROR", "", str(e)))

        avg_latency = total_latency / total if total else 0
        print(f"Meal type accuracy:  {meal_correct}/{total} ({meal_correct/total*100:.1f}%)")
        print(f"Cuisine accuracy:    {cuisine_correct}/{total} ({cuisine_correct/total*100:.1f}%)")
        print(f"Both correct:        {both_correct}/{total} ({both_correct/total*100:.1f}%)")
        print(f"Avg latency:         {avg_latency:.0f}ms")
        print(f"Total time:          {total_latency/1000:.1f}s")

        if failures:
            print(f"\nFailures ({len(failures)}):")
            for title, axis, expected, got in failures:
                print(f"  {title[:40]:40s} {axis:8s} expected='{expected}' got='{got}'")


def main():
    parser = argparse.ArgumentParser(description="Benchmark recipe categorization across LLMs")
    parser.add_argument("--url", default="http://localhost:1234/v1", help="LLM API URL")
    parser.add_argument("--models", nargs="+", help="Model names to test (default: auto-detect)")
    args = parser.parse_args()

    if not args.models:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=args.url, api_key="not-needed")
            models_resp = client.models.list()
            args.models = [m.id for m in models_resp.data]
            print(f"Detected models: {', '.join(args.models)}")
        except Exception as e:
            print(f"Could not detect models at {args.url}: {e}")
            sys.exit(1)

    run_benchmark(args.url, args.models)


if __name__ == "__main__":
    main()
