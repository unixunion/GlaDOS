# Shopping List & Pantry

A durable shopping list and pantry inventory system with voice commands, interactive display views, expiry tracking, and recipe integration.

Data is stored as JSON in `plugin_data/pantry/` and survives restarts and context wipes.

## Shopping List

### Adding Items

| Say this | What happens |
|----------|-------------|
| "add eggs to the shopping list" | Adds eggs (auto-categorized as dairy) |
| "put milk on the list" | Alternate phrasing |
| "we need butter" | Natural phrasing |
| "we're out of bread" | Adds to list + removes from pantry |
| "we're running low on rice" | Adds to list |
| "we buy eggs every two weeks" | Recurring item — auto re-adds every 14 days |

- Items are auto-categorized (dairy, produce, meat, bakery, dry goods, etc.) for grouped display
- Saying "we're out of X" also removes the item from the pantry if present
- Duplicate items are detected by fuzzy matching

### Viewing & Managing

| Say this | What happens |
|----------|-------------|
| "what's on the shopping list" | Shows interactive list on display + LLM summarizes |
| "show the shopping list" | Alternate phrasing |
| "what do we need to buy" | Alternate phrasing |
| "remove milk from the list" | Removes by fuzzy name match |
| "take eggs off the list" | Alternate phrasing |

### Post-Shopping

| Say this | What happens |
|----------|-------------|
| "we did the shopping" | Moves checked items to pantry (or all if none checked) |
| "shopping done" | Alternate phrasing |
| "we got everything" | Moves all items to pantry |
| "we got everything except eggs and butter" | Moves everything except named items |

- Only **checked** items move to pantry — unchecked items stay on the list
- If no items were checked (voice-only, no UI interaction), all items move
- Fuzzy matching: "except the chicken" matches "chicken breasts" on the list
- After completing, the **Put Away** guided view appears automatically
- Excepted items stay on the list with unchecked status

### Put Away Flow

After completing shopping, a guided view walks you through assigning a location to each item:

- Each item shows **quick-tap location buttons** (Fridge, Freezer Drawer 1, Dry Goods, etc.)
- **Suggested locations are highlighted in green** based on the item category (meat → Fridge, ice cream → Freezer)
- Tapping a location assigns the item, auto-estimates its expiry, and moves to the next item
- Once all items are assigned, the view switches to the full pantry
- If you skip it, a highlighted **"Put Away (3)"** button appears in the pantry toolbar whenever there are unassigned items

### Interactive Display

The shopping list view on the display (`http://<host>:5001`) shows:

- **Add item form** at the top — type name + optional quantity, hit Enter or click Add
- Items grouped by category (Dairy, Produce, Meat, etc.)
- Checkboxes to mark items as got/not-got (tap to toggle)
- Quantity badges
- **Recurring dropdown** per item — select Off, 3d, 7d, 10d, 14d, or 30d to set a recurring interval
- Remove button (X) per item
- "Done Shopping" button — moves checked items to pantry, keeps unchecked
- Progress indicator ("3 of 7 items")

### Recurring Items

Set recurring via the dropdown on each item in the display, or by voice ("we buy eggs every two weeks"). A recurring rule is created that auto-adds the item back to the shopping list on schedule.

Recurring rules persist across shopping cycles — completing shopping doesn't remove the rule. Removing the item from the list also removes its recurring rule.

## Pantry Inventory

### Storing Items

| Say this | What happens |
|----------|-------------|
| "I put the chicken in freezer drawer 2" | Records location |
| "the flour is in the dry goods cupboard" | Alternate phrasing |
| "store the milk in the fridge" | Alternate phrasing |

- Location matching is fuzzy — "freezer 2" matches "Freezer Drawer 2"
- If the item is on the shopping list, it's automatically removed

### Expiry Tracking

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

### Moving Items

| Say this | What happens |
|----------|-------------|
| "I moved the chicken to the freezer" | Updates location, re-estimates expiry if auto-estimated |
| "transfer the bread to the fridge" | Same behavior |

- Move is LLM-only (no NLP fast-path) — the LLM has context to distinguish "move existing item" from "store new item"
- The UI also provides a move dropdown per item in the pantry view

### Location Types

Each storage location has a type that affects shelf life estimation:

| Type | Icon | Examples |
|------|------|----------|
| `fridge` | ❄️ | Fridge |
| `freezer` | 🧊 | Freezer Drawer 1-3 |
| `room_temp` | 🏠 | Dry Goods Cupboard, Spices |

- Types are auto-inferred from location names when created
- Can be changed via the dropdown on each location header in the pantry UI

### Proactive Warnings

Once per day, GlaDOS checks for items expiring within 2 days and proactively announces them via TTS.

- **User-set expiry**: "Heads up — the bacon in the fridge expires tomorrow."
- **Estimated expiry**: "Heads up — the chicken in the fridge might be getting old." (softer language)

### Finding Items

| Say this | What happens |
|----------|-------------|
| "where is the flour" | Reports location and expiry if set |
| "do we have eggs" | Checks pantry, notes if on shopping list |
| "where did I put the chicken" | Alternate phrasing |
| "is there any butter" | Alternate phrasing |

### Viewing Pantry

| Say this | What happens |
|----------|-------------|
| "show me what's in the pantry" | Full pantry view on display |
| "what's in the fridge" | Filtered to fridge only |
| "what's in freezer drawer 1" | Filtered to specific location |
| "what food do we have" | Alternate phrasing |

### Interactive Display

The pantry view on the display shows:

- Collapsible location sections (tap to expand/collapse)
- **Per-location add item form** — type name, notes, and pick an expiry date directly
- Item name, notes, and **inline date picker** for expiry with colour coding (red/orange/yellow/green)
- Click any expiry date to change it; items without expiry show a date picker to set one
- Remove button (X) per item
- "Add Location" input at the bottom for managing locations
- "Expiring Soon" warning banner when items are within 3 days of expiry

### Managing Locations

Default locations: Fridge, Freezer Drawer 1-3, Dry Goods Cupboard.

| Say this | What happens |
|----------|-------------|
| "add a location called garage freezer" | Creates new location |
| "remove the dry goods cupboard location" | Removes (items become unassigned) |
| "rename freezer drawer 1 to top freezer" | Renames |

Locations can also be added via the display UI.

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
| "put the chicken tikka as a meal in the fridge" | Explicit ready_meal |
| "mark the chicken as a ready meal" | Reclassify existing item |
| "that's an ingredient not a meal" | Reclassify |

### LLM Classification

In hybrid/LLM mode, a fast lightweight model (same one used for knowledge query rewriting) runs a background classification after each store. This catches ambiguous items like "chicken tikka" that keyword matching might miss.

### How It Affects Suggestions

"What can we make?" now returns two sections:
1. **Ready to eat** — dishes already in the pantry (lasagna, frozen pizza)
2. **Recipes you can make** — recipe suggestions from ingredients

## Recipe Integration

| Say this | What happens |
|----------|-------------|
| "what can I make with what's in the pantry" | Ready meals + recipe suggestions from ingredients |
| "what can I make before things expire" | Prioritizes expiring ingredients |
| "suggest a meal" | General meal suggestion from pantry |
| "add the ingredients for that to the shopping list" | Adds missing recipe ingredients |
| "do we have the ingredients" | Checks pantry against active recipe |
| "what ingredients are we missing" | Lists what to buy |

- `suggest_meals_from_pantry` separates ready meals from ingredients, shows both sections
- Expiring items are prioritized in recipe suggestions
- `add_recipe_ingredients_to_list` checks what you already have and only adds missing ingredients
- `check_recipe_ingredients` compares the active recipe against pantry contents

## Shopping Modes (Sub-Contexts)

Voice-activated modes that lock GlaDOS into shopping-focused commands for faster batch operations.

### Planning Mode

| Say this | What happens |
|----------|-------------|
| "lets plan the shopping" | Enters planning mode |
| "lets plan shopping" | Alternate phrasing |
| "planning mode" | Short form |

While in planning mode:
- Just say the item name to add it — no need for "add X to the shopping list"
- "remove eggs" — removes item
- "3 of those" / "make that 5" — updates quantity of last-added item
- "show the list" — displays current list
- "done" / "that's everything" — exits planning mode

### Post-Shopping Mode

| Say this | What happens |
|----------|-------------|
| "back from shopping" | Enters post-shopping mode |
| "we're back from shopping" | Alternate phrasing |
| "unpack the shopping" / "lets put away the shopping" | Alternate phrasing |
| "back from the store" / "post shopping" | Alternate phrasing |

While in post-shopping mode:

| Say this | What happens |
|----------|-------------|
| "got the eggs" / "we got the eggs" | Marks item as bought (checked) |
| "didn't get milk" / "skip the butter" | Keeps item on list (unchecked) |
| "put the chicken in freezer drawer 2" | Stores in pantry with location + auto-estimated expiry |
| "chicken expires on the 24th" | Sets expiry date |
| "we got everything" | Marks all as bought, moves to pantry |
| "we got everything except the milk" | Moves all except named items to pantry |
| "done" / "finished" / "that's everything" | Completes shopping, exits mode |

**What happens on completion:**
- Only **checked** items move to pantry (unchecked stay on list)
- If nothing was checked (pure voice, no UI), all items move (backward compatible)
- The **Put Away** guided view appears on the display for assigning storage locations
- Fuzzy matching: "chicken" matches "chicken breasts", "milk" matches "full cream milk"

### Completing Shopping (without post-shopping mode)

You don't have to use post-shopping mode. These voice commands work anytime:

| Say this | What happens |
|----------|-------------|
| "we did the shopping" / "shopping done" | Moves checked items to pantry |
| "we got everything on the list" | Moves all items to pantry |
| "we got everything except eggs and butter" | Moves all except named items |
| "the shopping is complete" | Alternate phrasing |

The **"Done Shopping"** button is also available:
- In the shopping list view (sticky bar at the top with checked count)
- In the mobile shopping list (`/shopping`) footer
- Only active when at least one item is checked

### How it works

- Short commands in either mode are handled instantly (~5ms) without the LLM
- Unrecognized commands go to the LLM with a restricted tool set (only shopping/pantry tools)
- The display shows a mode indicator: "Planning" or "Post-Shopping" in orange
- Modes auto-timeout after inactivity (configurable, default 120 seconds)
- Say "done", "exit", "that's everything", "finished", or "cancel" to leave any mode

## Mobile Shopping List

Access the shopping list on your phone at `http://<glados-ip>:5001/shopping`.

### Features

- Mobile-optimized standalone page (not the full GlaDOS display)
- Add items with name + quantity
- Check items off as you shop (checkboxes)
- Grouped by category (Dairy, Produce, Meat, etc.)
- "Done Shopping" button
- **PWA** — add to home screen on iPhone/Android for app-like experience

### Offline Support

- **localStorage** caches the list — survives page refreshes and going offline
- **Service worker** caches the page itself — loads even without WiFi
- **SocketIO** syncs live when connected to home WiFi
- If you go offline at the store, the cached list stays visible and checkable
- When you reconnect at home, changes sync back to GlaDOS

### Setup

1. Open `http://<glados-ip>:5001/shopping` on your phone
2. On iPhone: tap Share > Add to Home Screen
3. On Android: tap the browser menu > Add to Home Screen

---

## Technical Details

### Data Storage

All data is stored as human-readable JSON in `plugin_data/pantry/`:

- `shopping_list.json` — items + recurring rules
- `pantry.json` — inventory items, storage locations, shelf life config

### Configuration

Works out of the box with sensible defaults. Optional overrides:

```yaml
plugins:
  - name: pantry_plugin
    config:
      shopping_mode_timeout: 120        # seconds before auto-exiting planning/post-shopping modes
      auto_estimate_expiry: true        # set false to disable shelf life estimation
      shelf_life_overrides:             # override default shelf life (days) per category
        meat: {fridge: 4, freezer: 120}
        dairy: {fridge: 14}
```

Storage locations, location types, and shelf life defaults can also be managed via the display UI.
