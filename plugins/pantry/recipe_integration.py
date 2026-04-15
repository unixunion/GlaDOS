"""Recipe integration tools for the pantry plugin.

Bridges the pantry and the recipes plugin: "what can I make with what's in
the pantry", "add the ingredients for this recipe", "do we have the
ingredients". Implemented as a mixin so it sits alongside the rest of the
pantry plugin's tool registrations.
"""

from datetime import date

from loguru import logger

from glados.context.activity import Activity
from glados.system.event_system import EventMessage

from plugins.pantry.nlp_handlers import (
    _add_recipe_ingredients_nlp_extract,
    _add_recipe_ingredients_nlp_response,
    _check_recipe_ingredients_nlp_response,
    _suggest_meals_nlp_response,
)


class RecipeIntegrationMixin:
    """Mixin providing recipe-aware pantry/shopping tools."""

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
                # Core: pantry-based meal suggestions — "pantry" is the anchor word
                "what can I make with what's in the pantry",
                "what can I cook from the pantry",
                "suggest a meal from the pantry",
                "suggest meals from pantry",
                "recipe suggestions from pantry",
                "meals from the pantry",
                "pantry meal suggestions",
                "suggest a meal from pantry ingredients",
                # Expiry-driven
                "what can I make before things expire",
                "cook something before it expires",
                # Ready meals / leftovers
                "do we have any left overs",
                "is anything ready to eat in the pantry",
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

        # Normalize ingredient names for better pantry matching
        try:
            from plugins.recipes.recipe_api import normalize_ingredient
            _normalize = normalize_ingredient
        except ImportError:
            _normalize = None

        have = []
        missing = []
        for ing in ingredient_lines:
            # Try normalized name first, then original
            found = False
            if _normalize:
                core, conf = _normalize(ing)
                if conf >= 0.85:
                    found = bool(self._find_pantry_items(core, strict=True))
            if not found:
                found = bool(self._find_pantry_items(ing, strict=True))
            if found:
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

    def suggest_meals_from_pantry(self, use_expiring_first: bool = True, expiring_items: list = None) -> dict:
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

        # Prioritize specific expiring items if provided, otherwise auto-detect
        expiring = []
        if expiring_items:
            expiring = list(expiring_items)
            others = [i["name"] for i in ingredient_items if i["name"] not in expiring_items]
            query_items = expiring + others
        elif use_expiring_first:
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
            from plugins.recipes.recipe_api import search_by_ingredients, safe_parse_list, normalize_ingredient
            import plugins.recipes.recipe_api as _recipe_mod
            # Normalize pantry item names for better recipe matching
            normalized_query = []
            for q in query_items[:15]:
                norm, conf = normalize_ingredient(q)
                normalized_query.append(norm if conf >= 0.85 else q)
            matches = search_by_ingredients(normalized_query) if normalized_query else []
            logger.info(f"[Pantry] Recipe suggestion: {len(matches)} matches from {len(query_items)} ingredients, {len(ready_meals)} ready meals")

            top = matches[:10]

            # Push results to display as recipe search view
            from plugins.recipes.recipe_api import _count_pantry_matches
            pantry_names = [i["name"].lower() for i in self._pantry["items"]]
            expiring_set = set(e.lower() for e in expiring) if expiring else set()
            display_results = []
            for m in top:
                ings = m.get("ingredients", [])
                have, missing = _count_pantry_matches(ings, pantry_names) if ings else (0, 0)
                matched = m.get("matched_ingredients", [])
                matched_expiring = [i for i in matched if i.lower() in expiring_set]
                display_results.append({
                    "title": m["title"],
                    "image_name": m.get("image_name"),
                    "ingredient_count": len(ings),
                    "have_count": have,
                    "missing_count": missing,
                    "matched_expiring": matched_expiring,
                })
            # Detect search method from results (semantic vs fuzzy)
            search_method = ""
            if top:
                search_method = top[0].get("search_method", "fuzzy")

            self.event_system.publish(EventMessage(
                role="display", name="recipe_search",
                content={
                    "title": "Recipes from Pantry",
                    "query": "pantry ingredients",
                    "results": display_results,
                    "ready_meals": ready_meals,
                    "search_method": search_method,
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
            # Strip trailing prep instructions: "fennel seeds, lightly crushed with..." → "fennel seeds"
            if "," in item_name:
                item_name = item_name.split(",")[0].strip()
            quantity = ing["quantity"]
            # Check if we already have it in the pantry (strict mode: "chicken thighs" ≠ "chicken breasts")
            if self._find_pantry_items(item_name, strict=True):
                skipped.append(item_name)
                continue
            # Check if already on shopping list
            if self._find_shopping_item(item_name):
                skipped.append(item_name)
                continue
            # Add to shopping list with parsed name and quantity (annotate metric if enabled)
            if quantity:
                try:
                    from plugins.recipes.recipe_api import annotate_metric, _metric_annotations_enabled
                    if _metric_annotations_enabled:
                        quantity = annotate_metric(quantity)
                except ImportError:
                    pass
            self.add_to_shopping_list(item=item_name, quantity=quantity)
            added.append(item_name)

        self._publish_shopping_list_display()
        logger.info(f"[Pantry] Added {len(added)} recipe ingredients, skipped {len(skipped)}")
        title = recipe_data.get("title", recipe_name)
        return {
            "status": "success",
            "recipe": title,
            "added": len(added),
            "added_items": added,
            "skipped": len(skipped),
            "skipped_items": skipped,
            "message": f"Added {len(added)} ingredients for {title}. "
                       f"Skipped {len(skipped)} items you already have.",
        }
