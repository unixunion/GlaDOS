# Meal Planning

Save favorite recipes, plan weekly meals, and generate optimized shopping lists that cover your planned meals plus staple ingredients that unlock the widest variety of additional recipes.

Data is stored in `plugin_data/meal_planner/`.

**Related pages**: [Recipes & Cooking](recipes.md) | [Shopping List](shopping.md) | [Pantry & Inventory](pantry.md)

## Favorites

Save recipes you like for quick access and meal planning.

| Say this | What happens |
|----------|-------------|
| "save this recipe" | Saves the currently selected recipe |
| "favorite this recipe" | Alternate phrasing |
| "I love this recipe" | Alternate phrasing |
| "remove from favorites" | Removes current recipe from favorites |
| "show my favorites" | Displays all saved favorites on screen |
| "what recipes have I saved" | Alternate phrasing |

- A heart button appears on every recipe view and in search results
- Favorites persist indefinitely — they're recipes you like, not a weekly plan
- Optional tags for organizing: "save this as italian" (future feature)

## Weekly Meal Plan

Plan what you're cooking this week, day by day.

| Say this | What happens |
|----------|-------------|
| "plan lasagna for monday" | Adds recipe to Monday |
| "add pasta to wednesday" | Alternate phrasing |
| "plan this for friday" | Plans the currently selected recipe |
| "remove monday's meal" | Clears Monday |
| "show the meal plan" | Displays weekly plan on screen |
| "what are we cooking this week" | Alternate phrasing |

- **Multiple meals per day** — add breakfast, lunch, and dinner to the same day
- Plan auto-clears at the start of each new week
- The meal plan dashboard card shows total meals planned
- Click a recipe in the plan to view it; click X to remove individual meals
- **"+" button** on each day row — tap to add a meal with inline search
- These tools use NLP fast-path for reliable execution (the LLM is bypassed for direct tool calls)

## Household Setup

Configure household size for quantity scaling.

| Say this | What happens |
|----------|-------------|
| "we're a family of three" | Sets 3 people (2 adults, 1 child assumed) |
| "two adults and one kid" | Explicit configuration |
| "it's just me" | 1 adult |

- Quantities are scaled based on household size when generating shopping lists
- Default: 2 adults, 0 children
- Child portions are scaled at 50% of adult portions
- Stored in `plugin_data/meal_planner/household.json`

## Smart Shopping List Generation

The core feature — generate an optimized shopping list from your meal plan.

| Say this | What happens |
|----------|-------------|
| "generate a shopping list" | Creates list from planned meals |
| "what do I need to buy this week" | Alternate phrasing |
| "auto generate shopping list" | Alternate phrasing |

Also available as a **"From Plan"** button in the shopping list UI and a **"Generate Shopping List"** button in the meal plan view.

### What It Does

1. **Collects ingredients** from all planned recipes
2. **Checks pantry** — skips what you already have
3. **Scales quantities** for your household size (2 adults + 1 kid = 1.25x a 4-serving recipe)
4. **Adds to shopping list** — items appear categorized and normalized
5. **Suggests staple ingredients** — bonus items that unlock the most additional recipes

### Staple Suggestions

After adding the required ingredients, the system suggests a few extra "staple" ingredients based on a greedy optimization:

> "Consider also getting: heavy cream, lemon juice, parmesan cheese — they'd unlock 28 more recipes."

The algorithm looks at what you'll have after shopping (pantry + new items) and finds which additional ingredients would make the most complete recipes cookable. This uses the frequency data from the 13,500-recipe dataset.

## Meal Suggestions

| Say this | What happens |
|----------|-------------|
| "suggest meals for the week" | AI picks diverse recipes |
| "help me plan the week" | Alternate phrasing |
| "what should we cook this week" | Alternate phrasing |

Suggestions prioritize:
- **Favorites** — recipes you've saved get a boost
- **Pantry match** — recipes where you already have most ingredients
- **Diversity** — avoids picking 5 recipes that all need the same things

## Proactive Planning

On your configured planning day (default: Sunday), after 10 AM, if no meals are planned:

> "It's Sunday — would you like to plan meals for the week? You have 12 saved favorites."

This can be disabled:
```yaml
plugins:
  - name: meal_planner
    config:
      proactive_suggestions: false
```

## Display Views

### Meal Plan View
- Day-grouped list (Mon-Sun) with recipe thumbnails
- Toolbar: "Favorites" and "Generate Shopping List" buttons
- Click a recipe to view it, X to remove from plan
- Dashboard card: "3/7 meals planned"

### Favorites View
- Grid of saved recipes with thumbnails
- Heart button to remove
- "+ Plan" button to add to the weekly plan

## Configuration

```yaml
plugins:
  - name: meal_planner
    config:
      default_servings: 4            # assumed recipe serving size for scaling
      staple_budget: 5               # how many bonus staple ingredients to suggest
      proactive_suggestions: true    # Sunday planning prompt
      planning_day: sunday           # which day to prompt
```

Household settings are configured via voice ("two adults and one kid") and stored in `plugin_data/meal_planner/household.json`.
