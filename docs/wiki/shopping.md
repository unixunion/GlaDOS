# Shopping List

A durable shopping list with voice commands, interactive display, mobile PWA, and recipe integration.

Data is stored as JSON in `plugin_data/pantry/shopping_list.json` and survives restarts.

## Adding Items

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
- **Ingredient normalization**: item names are automatically matched to known recipe ingredient names for better recipe integration (e.g., "chicken" → "chicken breast"). See [Recipes & Cooking — Ingredient Normalization](recipes.md#ingredient-normalization).

### Correcting Normalized Names

If GlaDOS normalizes an item name and you disagree:

| Say this | What happens |
|----------|-------------|
| "that's wrong" | Reverts the last item to its original name |
| "use the name I said" | Same — reverts to what you originally said |
| "rename that to chicken" | Renames the last added item |
| "I said chicken not chicken breast" | Reverts to "chicken" |

The original name is preserved as metadata, so reverting is always possible. You can also click to edit any item name in the display UI.

## Viewing & Managing

| Say this | What happens |
|----------|-------------|
| "what's on the shopping list" | Shows interactive list on display + LLM summarizes |
| "show the shopping list" | Alternate phrasing |
| "what do we need to buy" | Alternate phrasing |
| "remove milk from the list" | Removes by fuzzy name match |
| "take eggs off the list" | Alternate phrasing |

## Post-Shopping

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

## Interactive Display

The shopping list view on the display (`http://<host>:5001`) shows:

- **Add item form** at the top — type name + optional quantity, hit Enter or click Add
- **Autocomplete suggestions** — as you type (2+ characters), a dropdown shows matching ingredient names from the recipe database. Use arrow keys to navigate, Enter to select, or keep typing to ignore.
- **"From Plan" button** — generate shopping list from your [weekly meal plan](meal-planner.md)
- Items grouped by category (Dairy, Produce, Meat, etc.)
- Checkboxes to mark items as got/not-got (tap to toggle)
- Quantity badges
- **Recurring dropdown** per item — select Off, 3d, 7d, 10d, 14d, or 30d to set a recurring interval
- Remove button (X) per item
- "Done Shopping" button — moves checked items to pantry, keeps unchecked
- Progress indicator ("3 of 7 items")

## Recurring Items

Set recurring via the dropdown on each item in the display, or by voice ("we buy eggs every two weeks"). A recurring rule is created that auto-adds the item back to the shopping list on schedule.

Recurring rules persist across shopping cycles — completing shopping doesn't remove the rule. Removing the item from the list also removes its recurring rule.

## Shopping Modes

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

### How Modes Work

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

## Configuration

```yaml
plugins:
  - name: pantry_plugin
    config:
      shopping_mode_timeout: 120        # seconds before auto-exiting planning/post-shopping modes
```

See also: [Configuration — Pantry & Shopping List](configuration.md#pantry--shopping-list)
