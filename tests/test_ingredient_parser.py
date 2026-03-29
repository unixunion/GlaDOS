"""Ingredient parser test suite.

Tests quantity extraction, modifier separation, item normalization,
and non-food filtering.

Run with: pytest tests/test_ingredient_parser.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glados.nlp.ingredient_parser import (
    parse_ingredient,
    parse_ingredient_list,
    load_vocabulary_from_recipes,
)


@pytest.fixture(scope="module", autouse=True)
def _load_vocab():
    """Load recipe vocabulary once for the module."""
    try:
        # Need recipe data loaded first
        from plugins.recipes.recipe_api import recipes
        if recipes:
            load_vocabulary_from_recipes()
    except Exception:
        pass  # Tests still work without vocab, just no fuzzy matching


class TestQuantityExtraction:
    def test_tablespoons(self):
        r = parse_ingredient("2 tablespoons fresh lemon juice")
        assert r["quantity"] == "2 tablespoons"
        assert "lemon juice" in r["item"]

    def test_cup(self):
        r = parse_ingredient("1/2 cup mayonnaise")
        assert r["quantity"] == "1/2 cup"
        assert r["item"] == "mayonnaise"

    def test_mixed_fraction(self):
        r = parse_ingredient("1 1/2 cups all-purpose flour")
        assert "1 1/2" in r["quantity"]
        assert "flour" in r["item"]

    def test_count_only(self):
        r = parse_ingredient("4 soft seeded hamburger buns")
        assert r["quantity"] == "4"
        assert "hamburger buns" in r["item"]

    def test_no_quantity(self):
        r = parse_ingredient("Kosher salt")
        assert r["quantity"] is None
        assert "kosher salt" in r["item"]

    def test_teaspoons(self):
        r = parse_ingredient("2 teaspoons cayenne pepper")
        assert r["quantity"] == "2 teaspoons"
        assert "cayenne pepper" in r["item"]


class TestModifierExtraction:
    def test_fresh(self):
        r = parse_ingredient("2 tablespoons fresh lemon juice")
        assert r["modifier"] is not None
        assert "fresh" in r["modifier"]

    def test_finely_chopped(self):
        r = parse_ingredient("2 tablespoons finely chopped chives")
        assert "finely" in r["modifier"]
        assert "chopped" in r["modifier"]
        assert r["item"] == "chives"

    def test_no_modifier(self):
        r = parse_ingredient("1 cup buttermilk")
        assert r["modifier"] is None

    def test_multiple_modifiers(self):
        r = parse_ingredient("4 skin-on skinless boneless chicken thighs")
        assert r["modifier"] is not None
        assert "boneless" in r["modifier"]


class TestParenthetical:
    def test_optional(self):
        r = parse_ingredient("3 tablespoons hot sauce, plus more for serving (optional)")
        assert r["item"] == "hot sauce"
        assert "(optional)" not in r["item"]

    def test_frying_note(self):
        r = parse_ingredient("Peanut or vegetable oil (for frying; about 8 cups)")
        assert "oil" in r["item"]
        assert "(for frying" not in r["item"]


class TestNonFood:
    def test_thermometer(self):
        r = parse_ingredient("A deep-fry thermometer")
        assert r["is_food"] is False

    def test_parchment(self):
        r = parse_ingredient("Parchment paper")
        assert r["is_food"] is False

    def test_food_is_food(self):
        r = parse_ingredient("2 cups all-purpose flour")
        assert r["is_food"] is True


class TestParseList:
    def test_filters_non_food(self):
        ingredients = [
            "2 cups flour",
            "A deep-fry thermometer",
            "1 cup sugar",
            "Parchment paper",
        ]
        results = parse_ingredient_list(ingredients)
        items = [r["item"] for r in results]
        assert "flour" in " ".join(items)
        assert "sugar" in " ".join(items)
        assert not any("thermometer" in i for i in items)
        assert not any("parchment" in i for i in items)

    def test_empty_list(self):
        assert parse_ingredient_list([]) == []

    def test_short_items_filtered(self):
        results = parse_ingredient_list(["", "a", "2"])
        assert len(results) == 0


class TestEdgeCases:
    def test_unicode_fractions(self):
        r = parse_ingredient("½ cup butter")
        assert r["quantity"] is not None
        assert "butter" in r["item"]

    def test_leading_dash(self):
        r = parse_ingredient("- 2 cups milk")
        assert r["quantity"] == "2 cups"
        assert "milk" in r["item"]

    def test_leading_bullet(self):
        r = parse_ingredient("• 1 tablespoon olive oil")
        assert "olive oil" in r["item"]

    def test_divided(self):
        r = parse_ingredient("2 cups sugar, divided")
        assert r["item"] == "sugar"
        assert "divided" not in r["item"]

    def test_empty_string(self):
        r = parse_ingredient("")
        assert r["item"] == ""
        assert r["quantity"] is None
