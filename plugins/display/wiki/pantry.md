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
| "we did the shopping" | Moves ALL items to pantry |
| "shopping done" | Alternate phrasing |
| "we got everything except eggs and butter" | Moves everything except named items to pantry |
| "we bought everything except the milk" | Named items stay on list |

- Bought items are moved to the pantry (location: unassigned — you can tell GlaDOS where you put things next)
- Excepted items stay on the list with unchecked status

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

### Proactive Warnings

Once per day, GlaDOS checks for items expiring within 2 days and proactively announces them via TTS: "Heads up — the bacon in the fridge expires tomorrow."

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

## Recipe Integration

| Say this | What happens |
|----------|-------------|
| "what can I make with what's in the pantry" | Searches recipes matching pantry contents |
| "what can I make before things expire" | Prioritizes expiring ingredients |
| "suggest a meal" | General meal suggestion from pantry |
| "add the ingredients for that to the shopping list" | Adds missing recipe ingredients |

- `suggest_meals_from_pantry` cross-references pantry items with the recipe database
- Expiring items are prioritized in recipe suggestions
- `add_recipe_ingredients_to_list` checks what you already have and only adds missing ingredients

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
| "unpack the shopping" | Alternate phrasing |

While in post-shopping mode:
- "got the eggs" — marks item as bought
- "didn't get milk" / "skip the butter" — keeps item on list
- "put chicken in freezer drawer 2" — stores item (same as normal)
- "chicken expires on the 24th" — sets expiry (same as normal)
- "done" — completes shopping (moves bought items to pantry), exits mode

### How it works

- A PRE_LLM hook intercepts commands at priority 5 (before memory, knowledge, NLP)
- Short commands (add/remove/got/store) are handled instantly without the LLM
- Unrecognized commands still go to the LLM but with a restricted tool set — only shopping/pantry tools are available
- The display shows a mode indicator: "Planning" or "Post-Shopping" in orange
- Say "done", "exit", or "that's everything" to leave any mode

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

## Data Storage

All data is stored as human-readable JSON in `plugin_data/pantry/`:

- `shopping_list.json` — items + recurring rules
- `pantry.json` — inventory items + storage locations

## Configuration

No configuration needed — the plugin works out of the box with sensible defaults. Storage locations can be managed via voice or the display UI.
