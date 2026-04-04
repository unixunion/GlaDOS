# Pantry & Inventory

Track what's in your pantry — storage locations, expiry dates, and shelf life estimation.

Data is stored as JSON in `plugin_data/pantry/pantry.json` and survives restarts.

**Related pages**: [Shopping List](shopping.md) | [Recipes & Cooking](recipes.md) | [Meal Planning](meal-planner.md)

## Storing Items

| Say this | What happens |
|----------|-------------|
| "I put the chicken in freezer drawer 2" | Records location |
| "the flour is in the dry goods cupboard" | Alternate phrasing |
| "store the milk in the fridge" | Alternate phrasing |

- Location matching is fuzzy — "freezer 2" matches "Freezer Drawer 2"
- If the item is on the shopping list, it's automatically removed

## Expiry Tracking

| Say this | What happens |
|----------|-------------|
| "the bacon expires on the 24th" | Sets expiry (assumes current/next month) |
| "eggs are best before 26th June" | Specific month |
| "the milk expires tomorrow" | Relative date |
| "what's expiring soon" | Lists items expiring within 7 days |
| "what expires this week" | Same, with explicit window |

- Expiry dates can be set when storing or separately
- The pantry display colour-codes items: red (expired/today), orange (1-3 days), yellow (4-7 days), green (ok)
- An "Expiring Soon" warning banner appears at the top of the pantry view

### Auto-Estimated Expiry (Shelf Life)

When items are added **without** an explicit expiry date, the system auto-estimates how long they'll keep based on:

1. **Item category** — meat (3d fridge / 180d freezer), dairy (10d fridge), produce (7d fridge), dry goods (365d), etc.
2. **Storage location type** — each location is classified as `fridge`, `freezer`, or `room_temp`
3. **Spice rack override** — items in a location with "spice" in the name get 365-day shelf life regardless of category

Estimated dates show with a `~` prefix and italic style in the UI to distinguish them from user-set dates.

**Key behaviors:**
- User-set expiry dates are **never** overridden — clearing an expiry is also treated as a user decision
- Moving an item between locations (e.g., fridge → freezer) **re-estimates** if the expiry was auto-estimated
- User-set dates are preserved on move
- Estimation only happens on add or move — no bulk re-estimation

**Three-layer configuration:**
1. Hardcoded defaults in `DEFAULT_SHELF_LIFE` (sensible out-of-the-box)
2. Config file overrides in `glados_config.yml` under `pantry_plugin.config.shelf_life_overrides`
3. UI overrides via the **Shelf Life** config panel (toolbar button in pantry view)

```yaml
# Example config override
plugins:
  - name: pantry_plugin
    config:
      auto_estimate_expiry: true  # set false to disable
      shelf_life_overrides:
        meat: {fridge: 4, freezer: 120}
```

## Moving Items

| Say this | What happens |
|----------|-------------|
| "I moved the chicken to the freezer" | Updates location, re-estimates expiry if auto-estimated |
| "transfer the bread to the fridge" | Same behavior |

- Move is LLM-only (no NLP fast-path) — the LLM has context to distinguish "move existing item" from "store new item"
- The UI also provides a move dropdown per item in the pantry view

## Storage Locations

Each storage location has a type that affects shelf life estimation:

| Type | Icon | Examples |
|------|------|----------|
| `fridge` | cold | Fridge |
| `freezer` | frozen | Freezer Drawer 1-3 |
| `room_temp` | ambient | Dry Goods Cupboard, Spices |

Default locations: Fridge, Freezer Drawer 1-3, Dry Goods Cupboard.

| Say this | What happens |
|----------|-------------|
| "add a location called garage freezer" | Creates new location |
| "remove the dry goods cupboard location" | Removes (items become unassigned) |
| "rename freezer drawer 1 to top freezer" | Renames |

- Types are auto-inferred from location names when created
- Can be changed via the dropdown on each location header in the pantry UI
- Locations can also be added via the display UI

## Ready Meals vs Ingredients

Pantry items are automatically classified as **ready meals** (complete dishes) or **ingredients** (raw components). This affects how meal suggestions work.

### Auto-Classification

When an item is stored, it's classified by keyword matching:
- **Ready meals**: lasagna, pizza, curry, stew, soup, leftover, casserole, burrito, tikka, etc.
- **Ingredients**: everything else (chicken, flour, eggs, butter, rice, etc.)

### Voice Override

| Say this | What happens |
|----------|-------------|
| "store the lasagna as a meal in the freezer" | Stored as ready_meal |
| "mark the chicken as a ready meal" | Reclassify existing item |
| "that's an ingredient not a meal" | Reclassify |

### LLM Classification

In hybrid/LLM mode, a fast lightweight model runs a background classification after each store. This catches ambiguous items like "chicken tikka" that keyword matching might miss.

## Finding Items

| Say this | What happens |
|----------|-------------|
| "where is the flour" | Reports location and expiry if set |
| "do we have eggs" | Checks pantry, notes if on shopping list |
| "where did I put the chicken" | Alternate phrasing |
| "is there any butter" | Alternate phrasing |

## Viewing Pantry

| Say this | What happens |
|----------|-------------|
| "show me what's in the pantry" | Full pantry view on display |
| "what's in the fridge" | Filtered to fridge only |
| "what's in freezer drawer 1" | Filtered to specific location |
| "what food do we have" | Alternate phrasing |

## Interactive Display

The pantry view on the display shows:

- Collapsible location sections (tap to expand/collapse)
- **Per-location add item form** — type name, notes, and pick an expiry date directly
- Item name, notes, and **inline date picker** for expiry with colour coding (red/orange/yellow/green)
- Click any expiry date to change it; items without expiry show a date picker to set one
- Remove button (X) per item
- "Add Location" input at the bottom for managing locations
- "Expiring Soon" warning banner when items are within 3 days of expiry

## Catalog Mode (Inventory Stocktake)

Open a fridge, freezer, or cupboard and call out what you see. GlaDOS updates the inventory idempotently — existing items get updated, new items get added with auto-estimated expiry, and items you don't mention can be flagged for removal.

| Say this | What happens |
|----------|-------------|
| "catalog the fridge" | Enters catalog mode for the fridge |
| "inventory freezer drawer 3" | Works with any location (fuzzy matched) |
| "stocktake the dry goods" | Alternate phrasing |

While in catalog mode:

| Say this | What happens |
|----------|-------------|
| "eggs" | Updates existing eggs (confirms they're still there) |
| "5 eggs" | Updates with quantity 5 |
| "two chicken sausages" | Adds new item with auto-estimated expiry |
| "no butter" / "remove the milk" | Removes item from location |
| "done" | Exits with reconciliation |

On exit, GlaDOS asks about items that were listed but you didn't mention — "I still have butter and cream cheese listed but you didn't mention them. Should I remove them?" Say yes to remove, no to keep.

- Word numbers work: "two", "three" etc. are converted to digits
- Location names are fuzzy-matched: "freezer 3" matches "Freezer Drawer 3", "freezer drawer three" also works
- Existing items are matched by fuzzy name — "chicken" matches "chicken breast"
- New items get auto-estimated expiry based on location type and food category
- The display shows the location's contents and updates live as you call out items

## Proactive Warnings

Once per day, GlaDOS checks for items expiring within 2 days and proactively announces them via TTS.

- **User-set expiry**: "Heads up — the bacon in the fridge expires tomorrow."
- **Estimated expiry**: "Heads up — the chicken in the fridge might be getting old." (softer language)
