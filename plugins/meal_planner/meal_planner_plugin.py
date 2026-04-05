"""Meal Planner — favorites, weekly meal planning, and smart shopping list generation.

Manages recipe favorites, weekly meal plans, household configuration, and
generates optimized shopping lists that cover planned meals plus staple
ingredients that unlock the widest variety of additional recipes.
"""
import json
import os
import re
import uuid
from datetime import datetime, date, timedelta

from loguru import logger
from rapidfuzz import fuzz

from glados.context.activity import Activity
from glados.system.event_system import EventMessage, EventHook
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_ABBREVS = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday",
                "fri": "friday", "sat": "saturday", "sun": "sunday"}


def _suggest_meals_nlp_response(result: dict) -> str:
    if result.get("status") == "empty":
        return result.get("message", "No suggestions available.")
    count = result.get("count", 0)
    return result.get("message", f"I've suggested {count} meals and put them on the screen.")


def _generate_list_nlp_response(result: dict) -> str:
    if result.get("status") == "empty":
        return result.get("message", "No meals planned yet.")
    return result.get("message", "Shopping list generated.")


def _show_plan_nlp_response(result: dict) -> str:
    if result.get("status") == "empty":
        return result.get("message", "No meals planned this week.")
    return result.get("message", "Here's the meal plan.")


class MealPlannerPlugin(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        self._data_dir = os.path.join("plugin_data", "meal_planner")
        os.makedirs(self._data_dir, exist_ok=True)

        self._favorites = self._load_json("favorites.json", {"favorites": []})
        self._meal_plan = self._load_json("meal_plan.json", {"current_week": "", "meals": []})
        self._household = self._load_json("household.json", {
            "adults": 2, "children": 0, "child_portion_factor": 0.5,
            "staple_ingredients": [], "auto_staples": True,
            "proactive_suggestions": True, "planning_day": "sunday",
        })

        self._proactive_prompted_today = False

        self.register_system_prompt(
            "MEAL PLANNER: A meal planning system is available. Users can save favorite recipes, "
            "plan meals for the week, and generate optimized shopping lists. "
            "Use save_favorite when the user wants to bookmark a recipe. "
            "Use generate_shopping_list to create a shopping list from the weekly meal plan."
        )

        self._register_favorite_tools()
        self._register_meal_plan_tools()
        self._register_household_tools()
        self._register_shopping_generation_tools()

        # Register views
        self.register_view("favorites", "plugins/meal_planner/views/favorites.js",
                           css_path="plugins/meal_planner/views/meal_plan.css")
        self.register_view("meal_plan", "plugins/meal_planner/views/meal_plan.js",
                           css_path="plugins/meal_planner/views/meal_plan.css", dashboard_card=True)

        # UI action handlers
        self.register_ui_action("meal_planner_action", self._on_ui_action)

    # -----------------------------------------------------------------------
    # Data persistence
    # -----------------------------------------------------------------------

    def _load_json(self, filename: str, default: dict) -> dict:
        path = os.path.join(self._data_dir, filename)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[MealPlanner] Failed to load {filename}: {e}")
        return default

    def _save_json(self, filename: str, data: dict):
        path = os.path.join(self._data_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_favorites(self):
        self._save_json("favorites.json", self._favorites)

    def _save_meal_plan(self):
        self._save_json("meal_plan.json", self._meal_plan)

    def _save_household(self):
        self._save_json("household.json", self._household)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _get_recipe_by_title(self, title: str) -> dict | None:
        """Find a recipe by fuzzy title match."""
        try:
            from plugins.recipes.recipe_api import recipes
            best, best_score = None, 0
            for r in recipes:
                score = fuzz.ratio(title.lower(), r["title"].lower())
                if score > best_score:
                    best, best_score = r, score
            return best if best_score >= 70 else None
        except ImportError:
            return None

    def _get_last_selected_recipe_title(self) -> str | None:
        """Get the title of the currently selected recipe."""
        try:
            from plugins.recipes.recipe_api import _last_selected_recipe
            if _last_selected_recipe:
                return _last_selected_recipe.get("title")
        except ImportError:
            pass
        return None

    def _is_favorite(self, title: str) -> bool:
        title_lower = title.lower()
        return any(f["title"].lower() == title_lower for f in self._favorites["favorites"])

    def _current_week_monday(self) -> str:
        """ISO date of this week's Monday."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        return monday.isoformat()

    def _ensure_current_week(self):
        """Auto-clear meal plan if we're in a new week."""
        current = self._current_week_monday()
        if self._meal_plan["current_week"] != current:
            self._meal_plan["current_week"] = current
            self._meal_plan["meals"] = []
            self._save_meal_plan()
            logger.info(f"[MealPlanner] New week started ({current}), cleared meal plan")

    # -----------------------------------------------------------------------
    # Favorites tools
    # -----------------------------------------------------------------------

    def _register_favorite_tools(self):
        self.register_tool(
            handler=self.save_favorite,
            description=(
                "Save a recipe to favorites. Use when the user likes a recipe and wants to "
                "keep it for later. If no recipe name given, saves the currently selected recipe."
            ),
            parameters={
                "recipe_name": {"type": "string", "description": "Recipe name to save. Omit to use current recipe."},
                "tags": {"type": "string", "description": "Optional comma-separated tags, e.g. 'italian, comfort'"},
            },
            required=[],
            intents=[
                "save this recipe",
                "favorite this recipe",
                "add this to favorites",
                "I love this recipe save it",
                "bookmark this recipe",
                "save that one to favorites",
                "add to my favorites",
                "keep this recipe in my favorites",
                # Casual / spoken forms
                "save this one",
                "that's a keeper save it",
                "add this recipe to my saved recipes",
                "save this to my recipes",
            ],
            process_output=False,
            activity=[Activity.COOKING, Activity.GENERAL],
        )

        self.register_tool(
            handler=self.remove_favorite,
            description="Remove a recipe from favorites.",
            parameters={
                "recipe_name": {"type": "string", "description": "Recipe name to remove from favorites"},
            },
            required=["recipe_name"],
            intents=[
                "remove from favorites",
                "unfavorite this recipe",
                "delete from favorites",
                "remove this from my favorites",
                "take this off my favorites",
                "remove that recipe from favorites",
                "unsave this recipe",
            ],
            process_output=False,
            activity=[Activity.COOKING, Activity.GENERAL],
        )

        self.register_tool(
            handler=self.show_favorites,
            description="Show saved favorite recipes on the display.",
            parameters={},
            required=[],
            intents=[
                "show my favorites",
                "show my favorite recipes",
                "what recipes have I saved",
                "show saved recipes",
                "my favorite recipes",
                "list my favorite recipes",
                "show me my bookmarked recipes",
                "what are my saved recipes",
                "pull up my favorites",
            ],
            process_output=True,
            activity=[Activity.COOKING, Activity.GENERAL],
        )

    def save_favorite(self, recipe_name: str = None, tags: str = None) -> dict:
        """Save a recipe to favorites."""
        if not recipe_name:
            recipe_name = self._get_last_selected_recipe_title()
        if not recipe_name:
            return {"status": "error", "message": "No recipe specified. Select a recipe first."}

        recipe = self._get_recipe_by_title(recipe_name)
        if not recipe:
            return {"status": "error", "message": f"Could not find recipe '{recipe_name}'."}

        title = recipe["title"]
        if self._is_favorite(title):
            return {"status": "exists", "message": f"{title} is already in your favorites."}

        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        self._favorites["favorites"].append({
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "added": datetime.now().isoformat(timespec="seconds"),
            "tags": tag_list,
        })
        self._save_favorites()
        logger.info(f"[MealPlanner] Saved favorite: {title}")
        return {
            "status": "saved",
            "title": title,
            "count": len(self._favorites["favorites"]),
            "message": f"Saved {title} to your favorites.",
        }

    def remove_favorite(self, recipe_name: str) -> dict:
        """Remove a recipe from favorites."""
        name_lower = recipe_name.lower()
        before = len(self._favorites["favorites"])
        self._favorites["favorites"] = [
            f for f in self._favorites["favorites"]
            if fuzz.ratio(f["title"].lower(), name_lower) < 70
        ]
        removed = before - len(self._favorites["favorites"])
        if removed:
            self._save_favorites()
            logger.info(f"[MealPlanner] Removed favorite: {recipe_name}")
            return {"status": "removed", "message": f"Removed {recipe_name} from favorites."}
        return {"status": "not_found", "message": f"{recipe_name} is not in your favorites."}

    def show_favorites(self) -> dict:
        """Show favorite recipes on display."""
        favorites = self._favorites["favorites"]
        if not favorites:
            return {"status": "empty", "message": "You don't have any saved favorites yet. "
                    "Select a recipe and say 'save this recipe' to add one."}

        # Get recipe details for display
        display_items = []
        for fav in favorites:
            recipe = self._get_recipe_by_title(fav["title"])
            display_items.append({
                "id": fav["id"],
                "title": fav["title"],
                "image_name": recipe.get("image_name") if recipe else None,
                "ingredient_count": len(recipe.get("core_ingredients", [])) if recipe else 0,
                "tags": fav.get("tags", []),
                "is_favorite": True,
            })

        self.event_system.publish(EventMessage(
            role="display", name="favorites",
            content={"title": "Favorite Recipes", "items": display_items},
            process_output=False,
        ))

        titles = [f["title"] for f in favorites[:5]]
        listing = ", ".join(titles)
        return {
            "status": "success",
            "count": len(favorites),
            "message": f"You have {len(favorites)} favorites. Showing them on screen. "
                       f"Top ones: {listing}.",
        }

    # -----------------------------------------------------------------------
    # Meal plan tools
    # -----------------------------------------------------------------------

    def _register_meal_plan_tools(self):
        self.register_tool(
            handler=self.plan_meal,
            description=(
                "Add a recipe to the weekly meal plan for a specific day. "
                "If no day specified, adds to the next unplanned day."
            ),
            parameters={
                "recipe_name": {"type": "string", "description": "Recipe to plan"},
                "day": {"type": "string", "description": "Day of week, e.g. 'monday', 'friday'"},
            },
            required=["recipe_name"],
            intents=[
                "plan lasagna for monday",
                "add pasta to wednesday",
                "plan this for friday",
                "let's have this on saturday",
                "cook this on tuesday",
                "add this to the meal plan",
                "plan this recipe for the week",
                "put this on the meal plan",
                "schedule this recipe for thursday",
                "let's make this on sunday",
                "plan that recipe for monday",
                # Casual / spoken
                "meal plan this for wednesday",
                "pop this on the plan for friday",
            ],
            process_output=False,
            activity=[Activity.COOKING, Activity.GENERAL],
        )

        self.register_tool(
            handler=self.remove_planned_meal,
            description="Remove a meal from the weekly plan.",
            parameters={
                "day": {"type": "string", "description": "Day to clear, e.g. 'monday'"},
                "recipe_name": {"type": "string", "description": "Recipe name to remove"},
            },
            required=[],
            intents=[
                "remove monday's meal from the plan",
                "cancel the lasagna from the meal plan",
                "clear friday on the meal plan",
                "remove that from the meal plan",
                "take lasagna off the plan",
                "unplan monday",
                "remove wednesday's dinner from the plan",
                "clear that day on the meal plan",
            ],
            process_output=False,
            activity=[Activity.COOKING, Activity.GENERAL],
        )

        self.register_tool(
            handler=self.show_meal_plan,
            description="Show the weekly meal plan on the display.",
            parameters={},
            required=[],
            intents=[
                # Core: display/show the existing plan
                "show the meal plan",
                "show me the meal plan",
                "show me this week's meals",
                "display the meal plan",
                "what's on the meal plan",
                "what have we got planned",
                "what meals are planned",
                "what did we plan for the week",
                "show planned meals",
                "pull up the meal plan",
            ],
            process_output=True,
            activity=[Activity.COOKING, Activity.GENERAL],
            nlp_response=_show_plan_nlp_response,
        )

    def plan_meal(self, recipe_name: str, day: str = None) -> dict:
        """Add a recipe to the weekly meal plan."""
        self._ensure_current_week()

        # Resolve recipe
        if not recipe_name or recipe_name.lower() in ("this", "this recipe", "that", "that one"):
            recipe_name = self._get_last_selected_recipe_title()
        if not recipe_name:
            return {"status": "error", "message": "No recipe specified."}

        recipe = self._get_recipe_by_title(recipe_name)
        if not recipe:
            return {"status": "error", "message": f"Could not find recipe '{recipe_name}'."}

        title = recipe["title"]

        # Resolve day
        if day:
            day_lower = day.lower().strip()
            day_resolved = _DAY_ABBREVS.get(day_lower[:3], day_lower)
            if day_resolved not in _DAYS:
                return {"status": "error", "message": f"Unknown day: {day}. Use monday-sunday."}
        else:
            # Find next unplanned day
            planned_days = {m["day"] for m in self._meal_plan["meals"]}
            day_resolved = next((d for d in _DAYS if d not in planned_days), None)
            if not day_resolved:
                return {"status": "full", "message": "All days are planned! Remove a meal first."}

        # Append to day (allow multiple meals per day: breakfast, lunch, dinner)
        self._meal_plan["meals"].append({
            "id": uuid.uuid4().hex[:8],
            "recipe_title": title,
            "day": day_resolved,
            "servings": None,
        })
        self._meal_plan["meals"].sort(key=lambda m: _DAYS.index(m["day"]))
        self._save_meal_plan()
        self._publish_meal_plan_display()

        logger.info(f"[MealPlanner] Planned '{title}' for {day_resolved}")
        return {
            "status": "planned",
            "title": title, "day": day_resolved,
            "message": f"Planned {title} for {day_resolved.capitalize()}.",
        }

    def remove_planned_meal(self, day: str = None, recipe_name: str = None) -> dict:
        """Remove a meal from the plan."""
        self._ensure_current_week()

        if day:
            day_lower = day.lower().strip()
            day_resolved = _DAY_ABBREVS.get(day_lower[:3], day_lower)
            self._meal_plan["meals"] = [m for m in self._meal_plan["meals"] if m["day"] != day_resolved]
        elif recipe_name:
            name_lower = recipe_name.lower()
            self._meal_plan["meals"] = [
                m for m in self._meal_plan["meals"]
                if fuzz.ratio(m["recipe_title"].lower(), name_lower) < 70
            ]
        else:
            return {"status": "error", "message": "Specify a day or recipe name to remove."}

        self._save_meal_plan()
        self._publish_meal_plan_display()
        return {"status": "removed", "message": "Updated the meal plan."}

    def show_meal_plan(self) -> dict:
        """Show the weekly meal plan on display."""
        self._ensure_current_week()
        self._publish_meal_plan_display()

        meals = self._meal_plan["meals"]
        if not meals:
            return {"status": "empty", "message": "No meals planned this week. "
                    "Say 'plan lasagna for Monday' or 'suggest meals for the week' to get started."}

        summary = []
        for m in meals:
            summary.append(f"{m['day'].capitalize()}: {m['recipe_title']}")
        return {
            "status": "success",
            "count": len(meals),
            "message": "This week's meal plan: " + ". ".join(summary) + ".",
        }

    def _publish_meal_plan_display(self):
        """Push meal plan view to display."""
        self._ensure_current_week()
        meals = self._meal_plan["meals"]

        # Enrich with recipe data
        display_meals = []
        for m in meals:
            recipe = self._get_recipe_by_title(m["recipe_title"])
            display_meals.append({
                "id": m["id"],
                "day": m["day"],
                "recipe_title": m["recipe_title"],
                "image_name": recipe.get("image_name") if recipe else None,
                "ingredient_count": len(recipe.get("core_ingredients", [])) if recipe else 0,
            })

        # Dashboard summary
        try:
            from plugins.meal_planner.optimizer import count_missing_ingredients
            pantry_names = self._get_pantry_names()
            planned_recipes = [m["recipe_title"] for m in meals]
            missing_count = count_missing_ingredients(planned_recipes, pantry_names)
        except Exception:
            missing_count = 0

        self.event_system.publish(EventMessage(
            role="display", name="meal_plan",
            content={
                "title": "Meal Plan",
                "week": self._meal_plan["current_week"],
                "meals": display_meals,
                "planned_count": len(meals),
                "missing_ingredients": missing_count,
                "favorites_count": len(self._favorites["favorites"]),
            },
            process_output=False,
        ))

    # -----------------------------------------------------------------------
    # Household tools
    # -----------------------------------------------------------------------

    def _register_household_tools(self):
        self.register_tool(
            handler=self.setup_household,
            description=(
                "Set up or update household size for meal planning quantity scaling. "
                "Specify number of adults and children."
            ),
            parameters={
                "adults": {"type": "integer", "description": "Number of adults"},
                "children": {"type": "integer", "description": "Number of children"},
            },
            required=[],
            intents=[
                "we're a family of three",
                "we're a family of four",
                "two adults and one kid",
                "there are two adults and two kids",
                "set up the household",
                "set up the household size",
                "it's just me cooking for one",
                "family of five",
                "there are three of us",
                "update the household to four people",
                "we cook for two adults and one child",
            ],
            process_output=False,
            activity=[Activity.GENERAL],
        )

    def setup_household(self, adults: int = None, children: int = None) -> dict:
        """Set household size for quantity scaling."""
        if adults is not None:
            self._household["adults"] = max(1, adults)
        if children is not None:
            self._household["children"] = max(0, children)
        self._save_household()
        a = self._household["adults"]
        c = self._household["children"]
        total = a + c
        logger.info(f"[MealPlanner] Household updated: {a} adults, {c} children")
        return {
            "status": "updated",
            "adults": a, "children": c,
            "message": f"Got it, household set to {total} — {a} adult{'s' if a != 1 else ''}"
                       + (f" and {c} kid{'s' if c != 1 else ''}" if c else "") + ".",
        }

    # -----------------------------------------------------------------------
    # Shopping list generation
    # -----------------------------------------------------------------------

    def _register_shopping_generation_tools(self):
        self.register_tool(
            handler=self.generate_shopping_list,
            description=(
                "Generate an optimized shopping list from the weekly meal plan. "
                "Cross-references pantry to skip what we have, scales quantities for "
                "household size, and suggests staple ingredients that unlock more recipes."
            ),
            parameters={},
            required=[],
            intents=[
                "generate a shopping list from the meal plan",
                "create a shopping list from the meal plan",
                "what do I need to buy for the meal plan",
                "what do we need to buy for the week's meals",
                "auto generate shopping list from the plan",
                "generate shopping list from the meal plan",
                "build a shopping list from the plan",
                "make a shopping list for the planned meals",
                "shopping list for this week's meal plan",
                "what ingredients do I need for the meal plan",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_threshold=0.5,
            nlp_response=_generate_list_nlp_response,
        )

        self.register_tool(
            handler=self.suggest_weekly_meals,
            description=(
                "Suggest meals for the week based on favorites, pantry contents, "
                "and ingredient optimization. Picks diverse recipes that maximize "
                "use of existing ingredients."
            ),
            parameters={
                "count": {"type": "integer", "description": "Number of meals to suggest (default: 5)"},
            },
            required=[],
            intents=[
                # Core: generate/suggest new meals — "suggest" is the anchor
                "suggest meals for the week",
                "suggest what to cook this week",
                "suggest a weekly menu",
                "suggest a weekly meal plan",
                "recommend meals for the week",
                "give me meal suggestions for the week",
                "auto suggest meals for the week",
                "help me decide what to cook this week",
                "fill in the meal plan with suggestions",
                "suggest dinners for the week",
                "plan meals for the week",
                "suggest a healthy meal plan for the week",
                "plan a healthy week of meals",
            ],
            process_output=True,
            activity=[Activity.GENERAL, Activity.COOKING],
            nlp_threshold=0.5,
            nlp_response=_suggest_meals_nlp_response,
        )

    def _get_pantry_names(self) -> list[str]:
        """Get lowercased pantry item names."""
        try:
            from plugins.pantry.pantry_plugin import PantryPlugin
            return [i["name"].lower() for i in PantryPlugin()._pantry["items"]]
        except Exception:
            return []

    def generate_shopping_list(self) -> dict:
        """Generate optimized shopping list from meal plan."""
        self._ensure_current_week()
        meals = self._meal_plan["meals"]
        if not meals:
            return {"status": "empty", "message": "No meals planned this week. Plan some meals first."}

        planned_titles = [m["recipe_title"] for m in meals]
        pantry_names = self._get_pantry_names()

        try:
            from plugins.meal_planner.optimizer import generate_optimized_list
            result = generate_optimized_list(
                planned_titles=planned_titles,
                pantry_items=set(pantry_names),
                household=self._household,
                staple_budget=self.plugin_config.get("staple_budget", 5),
            )
        except Exception as e:
            logger.error(f"[MealPlanner] Optimizer failed: {e}")
            return {"status": "error", "message": f"Could not generate list: {e}"}

        # Add required items to shopping list
        added = 0
        try:
            from plugins.pantry.pantry_plugin import PantryPlugin
            pp = PantryPlugin()
            for item in result["required_items"]:
                res = pp.add_to_shopping_list(
                    item=item["ingredient"],
                    quantity=item.get("quantity"),
                )
                if res.get("status") == "added":
                    added += 1
        except Exception as e:
            logger.warning(f"[MealPlanner] Failed to add items to shopping list: {e}")

        # Build message
        parts = [f"Added {added} ingredients to the shopping list for {len(meals)} planned meals."]
        if result.get("skipped"):
            parts.append(f"Skipped {result['skipped']} items already in pantry or on the list.")
        if result.get("staple_suggestions"):
            staples = result["staple_suggestions"][:3]
            staple_names = ", ".join(s["ingredient"] for s in staples)
            parts.append(f"Consider also getting: {staple_names} — "
                         f"they'd unlock {sum(s['unlocks'] for s in staples)} more recipes.")

        return {
            "status": "success",
            "added": added,
            "skipped": result.get("skipped", 0),
            "staple_suggestions": result.get("staple_suggestions", []),
            "message": " ".join(parts),
        }

    def suggest_weekly_meals(self, count: int = 5) -> dict:
        """Suggest diverse meals for the week from favorites + pantry optimization."""
        self._ensure_current_week()
        pantry_names = self._get_pantry_names()

        try:
            from plugins.meal_planner.optimizer import suggest_meals
            suggestions = suggest_meals(
                favorites=[f["title"] for f in self._favorites["favorites"]],
                pantry_items=set(pantry_names),
                count=min(count, 7),
            )
        except Exception as e:
            logger.error(f"[MealPlanner] Suggestion failed: {e}")
            return {"status": "error", "message": f"Could not generate suggestions: {e}"}

        if not suggestions:
            return {"status": "empty", "message": "No meal suggestions available. "
                    "Try saving some favorite recipes first."}

        # Show on display
        display_items = []
        for s in suggestions:
            display_items.append({
                "title": s["title"],
                "image_name": s.get("image_name"),
                "pantry_match_pct": s.get("pantry_match_pct", 0),
                "missing_count": s.get("missing_count", 0),
                "is_favorite": s.get("is_favorite", False),
            })

        self.event_system.publish(EventMessage(
            role="display", name="meal_plan",
            content={
                "title": "Suggested Meals",
                "suggestions": display_items,
                "week": self._meal_plan["current_week"],
                "meals": [],
                "planned_count": 0,
                "favorites_count": len(self._favorites["favorites"]),
            },
            process_output=False,
        ))

        titles = [s["title"] for s in suggestions[:5]]
        return {
            "status": "success",
            "count": len(suggestions),
            "message": f"Here are {len(suggestions)} meal suggestions based on your favorites "
                       f"and what's in the pantry: {', '.join(titles)}. "
                       f"Say 'plan' followed by a recipe name and day to add it to your plan.",
        }

    # -----------------------------------------------------------------------
    # UI action handler
    # -----------------------------------------------------------------------

    def _on_ui_action(self, event: EventMessage):
        """Handle UI actions from display views."""
        data = event.content if isinstance(event.content, dict) else {}
        action = data.get("action")

        if action == "toggle_favorite":
            recipe_name = data.get("recipe_name", "")
            if self._is_favorite(recipe_name):
                self.remove_favorite(recipe_name)
            else:
                self.save_favorite(recipe_name)
            # Refresh whichever view is showing
            self._publish_favorites_state()

        elif action == "show_favorites":
            self.show_favorites()

        elif action == "show_meal_plan":
            self.show_meal_plan()

        elif action == "plan_meal":
            self.plan_meal(
                recipe_name=data.get("recipe_name", ""),
                day=data.get("day"),
            )

        elif action == "remove_planned_meal":
            meal_id = data.get("meal_id")
            if meal_id:
                # Remove specific meal by ID (from UI)
                self._ensure_current_week()
                self._meal_plan["meals"] = [m for m in self._meal_plan["meals"] if m["id"] != meal_id]
                self._save_meal_plan()
                self._publish_meal_plan_display()
            else:
                self.remove_planned_meal(
                    day=data.get("day"),
                    recipe_name=data.get("recipe_name"),
                )

        elif action == "generate_shopping_list":
            result = self.generate_shopping_list()
            # TTS the result
            if result.get("message"):
                self.event_system.publish(EventMessage(
                    "tool", "meal_planner", result["message"], process_output=True
                ))

        elif action == "get_state":
            self._publish_dashboard_data()

    def _publish_favorites_state(self):
        """Push current favorite status for UI refresh."""
        fav_titles = {f["title"].lower() for f in self._favorites["favorites"]}
        self.event_system.publish(EventMessage(
            role="display", name="favorites_state",
            content={"favorite_titles": list(fav_titles)},
            process_output=False,
        ))

    def _publish_dashboard_data(self):
        """Push dashboard summary data."""
        self._ensure_current_week()
        self.event_system.publish(EventMessage(
            role="display", name="dashboard_data",
            content={
                "meal_planner": {
                    "planned": len(self._meal_plan["meals"]),
                    "favorites": len(self._favorites["favorites"]),
                }
            },
            process_output=False,
        ))

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self):
        self.event_system.subscribe(
            "ui.meal_planner_action",
            EventHook("meal_planner_ui", callback=self._on_ui_action, priority=5),
        )

        # Proactive planning suggestion on configured day
        self.event_system.subscribe(
            "system.tick",
            EventHook("meal_planner_tick", callback=self._on_tick, priority=1),
        )

        logger.success(f"[MealPlanner] Started — {len(self._favorites['favorites'])} favorites, "
                       f"{len(self._meal_plan['meals'])} planned meals")

    def _on_tick(self, event: EventMessage):
        """Check for proactive planning suggestions."""
        if not self._household.get("proactive_suggestions", True):
            return
        if self._proactive_prompted_today:
            return

        now = datetime.now()
        if now.hour < 10:
            return

        planning_day = self._household.get("planning_day", "sunday")
        today_name = now.strftime("%A").lower()
        if today_name != planning_day:
            return

        self._ensure_current_week()
        if self._meal_plan["meals"]:
            return  # already has meals planned

        self._proactive_prompted_today = True
        self.event_system.publish(EventMessage(
            "tool", "meal_planner",
            f"It's {planning_day.capitalize()} — would you like to plan meals for the week? "
            f"You have {len(self._favorites['favorites'])} saved favorites.",
            process_output=True,
        ))

    def stop(self):
        pass
