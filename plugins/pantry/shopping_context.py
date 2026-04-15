"""Shopping sub-context state machine for the pantry plugin.

Implements the planning / post-shopping / catalog modes as a mixin so the
stateful voice-command logic lives separately from the plugin's tool
registrations and persistence code.

The mixin expects the consuming class to provide the plugin state:
`_shopping_list`, `_pantry`, `_shopping_mode`, `_catalog_mode`,
`_last_added_item`, `_shopping_mode_last_activity`, `event_system`, plus
the tool methods (`remove_from_shopping_list`, `complete_shopping`,
`store_item`, `set_expiry`, `show_shopping_list`, `add_to_shopping_list`)
and helpers (`_save_shopping_list`, `_save_pantry`, `_find_shopping_item`,
`_find_location`, `_publish_shopping_list_display`, `_publish_pantry_display`).
"""

import re
from datetime import datetime

from loguru import logger
from rapidfuzz import fuzz

from glados.nlp.extractors import (
    ALL_EXIT_WORDS, CANCEL_WORDS, CONFIRM_WORDS, word_to_number,
    _ONES as _WORD_NUMBERS,
)
from glados.system.event_system import EventMessage

from plugins.pantry.nlp_handlers import (
    _remove_from_list_nlp_response,
    _set_expiry_nlp_response,
    _store_item_nlp_response,
)


class ShoppingContextMixin:
    """Mixin providing the shopping/post-shopping/catalog sub-context handlers."""

    _PLANNING_TRIGGERS = [
        "lets plan the shopping", "lets plan shopping", "shopping planning mode",
        "plan the shopping list", "lets make a shopping list", "plan shopping",
        "planning mode", "start planning the shopping", "shopping mode",
        "lets plan the list", "shopping list planning",
    ]
    _POST_SHOPPING_TRIGGERS = [
        "we're back from shopping", "back from the shops", "back from shopping",
        "post shopping", "lets put away the shopping", "unpack the shopping",
        "post shopping mode", "back from the store",
    ]
    _EXIT_TRIGGERS = ALL_EXIT_WORDS

    # Tool names allowed in each mode (for LLM tool_override)
    _PLANNING_TOOLS = [
        "add_to_shopping_list", "remove_from_shopping_list", "show_shopping_list",
        "suggest_meals_from_pantry", "find_item", "check_expiring",
    ]
    _POST_SHOPPING_TOOLS = [
        "complete_shopping", "store_item", "set_expiry", "find_item",
        "show_pantry", "show_shopping_list",
    ]
    _CATALOG_TRIGGERS = [
        # Direct commands
        "catalog the", "catalogue the", "inventory the", "stocktake the",
        "catalog mode", "catalogue mode", "stocktake mode", "inventory mode",
        # Contractions and casual forms
        "let's catalog the", "let's catalogue the", "let's inventory the",
        "let's stocktake the", "lets catalog the", "lets inventory the",
        "lets catalogue the", "lets stocktake the",
        # "of" phrasing (reported gap: "inventory of the fridge" didn't match)
        "inventory of the", "catalog of the", "catalogue of the", "stocktake of the",
        # Polite / interrogative forms
        "can you catalog the", "can you inventory the",
        "do a stocktake of the", "do an inventory of the",
        # AU/ZA casual: "let's go through the fridge"
        "go through the", "let's go through the", "lets go through the",
        # UK: "sort out the fridge", "do the fridge"
        "sort out the", "let's sort out the",
    ]
    _CATALOG_TOOLS = ["store_item", "find_item", "show_pantry"]

    _SHOPPING_MODE_TIMEOUT_DEFAULT = 60  # seconds of inactivity before auto-exiting mode

    @staticmethod
    def _clean_voice_text(text: str) -> str:
        """Clean Whisper transcription artifacts for trigger matching."""
        return re.sub(r"[.!?,;]+$", "", text.strip().lower()).strip()

    def _shopping_context_hook(self, ctx):
        """PRE_LLM hook: intercept commands when in a shopping sub-context."""
        text = self._clean_voice_text(ctx.user_text)

        # --- Handle active catalog mode (checked first, independent of shopping_mode) ---
        if self._catalog_mode:
            # Handle reconciliation yes/no
            if self._catalog_mode.get("awaiting_reconciliation"):
                text_lower = text.lower().strip()
                if text_lower in CONFIRM_WORDS or text_lower == "remove them":
                    ids_to_remove = self._catalog_mode["unmentioned_ids"]
                    self._pantry["items"] = [i for i in self._pantry["items"] if i["id"] not in ids_to_remove]
                    self._save_pantry()
                    self._finish_catalog_exit(ctx, self._catalog_mode["added"],
                                              self._catalog_mode["updated"],
                                              self._catalog_mode["removed"], len(ids_to_remove))
                else:
                    self._finish_catalog_exit(ctx, self._catalog_mode["added"],
                                              self._catalog_mode["updated"],
                                              self._catalog_mode["removed"], 0)
                return

            # Strip leading filler from speech ("okay done" → "done", "no we're finished" → "we're finished")
            cleaned = re.sub(r"^(?:okay|ok|no|yes|right|so|well|um|uh),?\s*", "", text, flags=re.IGNORECASE).strip() or text
            exit_match = any(cleaned == t or cleaned.startswith(t) or fuzz.ratio(cleaned, t) >= 80 for t in self._EXIT_TRIGGERS)
            if not exit_match:
                # Also check the original text
                exit_match = any(text == t or text.startswith(t) or fuzz.ratio(text, t) >= 80 for t in self._EXIT_TRIGGERS)
            if exit_match:
                # "cancel"/"abort" = exit without reconciliation, "done"/"finished" = exit with reconciliation
                is_cancel = any(cleaned.startswith(w) or cleaned == w for w in CANCEL_WORDS)
                if is_cancel:
                    added = self._catalog_mode["added"]
                    updated = self._catalog_mode["updated"]
                    removed = self._catalog_mode["removed"]
                    self._finish_catalog_exit(ctx, added, updated, removed, 0)
                else:
                    self._exit_catalog_mode(ctx)
                return
            self._handle_catalog_command(text, ctx)
            self._shopping_mode_last_activity = datetime.now()
            return

        # --- Check for mode entry (shopping/catalog) ---
        if not self._shopping_mode:
            if any(text.startswith(t) or text == t for t in self._PLANNING_TRIGGERS):
                self._shopping_mode = "planning"
                self._shopping_mode_last_activity = datetime.now()
                self._publish_shopping_list_display()
                self.event_system.publish(EventMessage(
                    "status", "shopping_mode", {"mode": "planning"}
                ))
                ctx.tts_queue.put("Shopping planning mode. Just say the item name to add it, or remove followed by the item.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                logger.info("[Pantry] Entered planning mode")
                return

            if any(fuzz.ratio(text, t) >= 80 or text.startswith(t) for t in self._POST_SHOPPING_TRIGGERS):
                self._shopping_mode = "post_shopping"
                self._shopping_mode_last_activity = datetime.now()
                self._publish_shopping_list_display()
                self.event_system.publish(EventMessage(
                    "status", "shopping_mode", {"mode": "post_shopping"}
                ))
                ctx.tts_queue.put("Post-shopping mode. Tell me what you got and where you put things. Say done when finished.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                logger.info("[Pantry] Entered post-shopping mode")
                return

            if any(text.startswith(t) for t in self._CATALOG_TRIGGERS):
                self._enter_catalog_mode(text, ctx)
                return

            return  # Not in any mode

        # --- Check for mode exit ---
        if any(text == t or text.startswith(t) or fuzz.ratio(text, t) >= 80 for t in self._EXIT_TRIGGERS):
            if self._shopping_mode == "post_shopping":
                result = self.complete_shopping()
                moved = result.get("moved", 0)
                remaining = result.get("remaining", 0)
                ctx.tts_queue.put(f"Done. Moved {moved} items to the pantry. {remaining} items remain on the list.")
            else:
                count = len(self._shopping_list["items"])
                ctx.tts_queue.put(f"Done planning. You have {count} items on the shopping list.")
            ctx.tts_queue.put("<EOS>")
            self.event_system.publish(EventMessage(
                "status", "shopping_mode", {"mode": None}
            ))
            logger.info(f"[Pantry] Exited {self._shopping_mode} mode")
            self._shopping_mode = None
            ctx.handled = True
            return

        # --- Handle commands within the active mode ---
        handled = False
        if self._shopping_mode == "planning":
            handled = self._handle_planning_command(text, ctx)
        elif self._shopping_mode == "post_shopping":
            handled = self._handle_post_shopping_command(text, ctx)

        if handled:
            self._shopping_mode_last_activity = datetime.now()

        if not handled:
            # Command not recognized by short-form handlers — pass through to LLM
            # but with restricted tool set
            tool_names = self._PLANNING_TOOLS if self._shopping_mode == "planning" else self._POST_SHOPPING_TOOLS
            mode_label = "shopping planning" if self._shopping_mode == "planning" else "post-shopping"

            # Build the filtered tool list for the LLM
            from glados.system.plugin import PluginSystem
            from glados.llm.client_type import ClientType
            all_tools = PluginSystem().get_available_tools(architecture=ClientType.OPENAI)
            filtered = [t for t in all_tools if isinstance(t, dict) and
                        t.get("function", {}).get("name") in tool_names]
            ctx.extra["tool_override"] = filtered

            # Inject mode context
            mode_prompt = (
                f"You are in {mode_label} mode. Only use the available shopping/pantry tools. "
                f"Help the user manage their shopping list and pantry."
            )
            if ctx.memory_context:
                ctx.memory_context = mode_prompt + "\n\n" + ctx.memory_context
            else:
                ctx.memory_context = mode_prompt

    def _handle_planning_command(self, text: str, ctx) -> bool:
        """Handle short commands in planning mode. Returns True if handled."""
        # "remove X"
        if text.startswith("remove ") or text.startswith("delete "):
            item = re.sub(r"^(?:remove|delete)\s+(?:the\s+)?", "", text).strip()
            if item:
                result = self.remove_from_shopping_list(item)
                msg = _remove_from_list_nlp_response(result)
                ctx.tts_queue.put(msg)
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        # "make that 6" / "made that six bananas" / "6 of those" / "actually 3"
        qty_match = re.match(
            r"(?:ma[dk]e\s+(?:that|it)\s+|actually\s+)(\w+)(?:\s+.*)?$"
            r"|(\w+)\s+of\s+(?:those|them)",
            text
        )
        if qty_match and self._last_added_item:
            raw_qty = (qty_match.group(1) or qty_match.group(2) or "").strip()
            num = word_to_number(raw_qty)
            if num is not None or raw_qty.isdigit():
                qty = str(num) if num is not None else raw_qty
                self._last_added_item["quantity"] = qty
                self._save_shopping_list()
                self._publish_shopping_list_display()
                ctx.tts_queue.put(f"Updated to {qty} {self._last_added_item['name']}.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        # "show the list" / "list"
        if text in ("show the list", "list", "show list", "what's on the list"):
            self.show_shopping_list()
            ctx.tts_queue.put(f"You have {len(self._shopping_list['items'])} items on the list.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        # Default: treat as "add X" — bare item names
        # But first, reject text that's clearly not a shopping item:
        # - Too long (>6 words is probably a sentence, not an item)
        # - Contains verbs/pronouns that indicate conversation, not items
        words = text.split()
        non_item_patterns = re.compile(
            r"\b(i think|i want|why|how|what|when|where|because|that's why|"
            r"this is|they|my|your|we need to|can you|could you|please help|"
            r"it's|cancelled|cancel|beep|gladys|glados)\b", re.IGNORECASE
        )
        if len(words) > 6 or non_item_patterns.search(text):
            # Doesn't look like a shopping item — pass to LLM with restricted tools
            return False

        # Strip leading "add" / "and" / "also"
        item_text = re.sub(r"^(?:add|and|also|plus)\s+(?:some\s+)?", "", text).strip()
        if item_text and len(item_text) > 1:
            # Split "X and Y" or "X, Y and Z" into separate items
            items = re.split(r"\s*(?:,\s*(?:and\s+)?|\s+and\s+)\s*", item_text)
            items = [i.strip() for i in items if i.strip() and len(i.strip()) > 1]
            if not items:
                return False
            added_names = []
            for item in items:
                # Strip leading "a/an/some"
                item = re.sub(r"^(?:a|an|some)\s+", "", item).strip()
                if item:
                    self.add_to_shopping_list(item)
                    self._last_added_item = next(
                        (i for i in self._shopping_list["items"] if i["name"].lower() == item.lower()), None
                    )
                    added_names.append(item)
            if added_names:
                listing = ", ".join(added_names)
                ctx.tts_queue.put(f"Added {listing} to the shopping list.")
                ctx.tts_queue.put("<EOS>")
                ctx.handled = True
                return True

        return False

    def _handle_post_shopping_command(self, text: str, ctx) -> bool:
        """Handle short commands in post-shopping mode. Returns True if handled."""
        # "got X" / "got the X" / "yes" (marks last mentioned)
        got_match = re.match(r"(?:got|got\s+the|we\s+got|yes)\s*(.*)", text)
        if got_match:
            item_name = got_match.group(1).strip()
            if item_name:
                match = self._find_shopping_item(item_name)
                if match:
                    match["got"] = True
                    self._save_shopping_list()
                    self._publish_shopping_list_display()
                    ctx.tts_queue.put(f"Marked {match['name']} as got.")
                    ctx.tts_queue.put("<EOS>")
                    ctx.handled = True
                    return True

        # "didn't get X" / "no X" / "not the X" / "skip X"
        skip_match = re.match(r"(?:didn't\s+get|no|not\s+the|skip|not)\s+(.*)", text)
        if skip_match:
            item_name = skip_match.group(1).strip()
            if item_name:
                match = self._find_shopping_item(item_name)
                if match:
                    match["got"] = False
                    self._save_shopping_list()
                    self._publish_shopping_list_display()
                    ctx.tts_queue.put(f"{match['name']} stays on the list.")
                    ctx.tts_queue.put("<EOS>")
                    ctx.handled = True
                    return True

        # "put X in Y" — delegate to store_item
        store_match = re.search(r"(?:put|stored|placed)\s+(?:the\s+)?(.+?)\s+(?:in|into)\s+(?:the\s+)?(.+)", text)
        if store_match:
            item = store_match.group(1).strip()
            location = store_match.group(2).strip()
            result = self.store_item(item, location)
            ctx.tts_queue.put(_store_item_nlp_response(result))
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        # "X expires Y" — delegate to set_expiry
        expiry_match = re.search(r"(.+?)\s+(?:expires?|best\s+before|use\s+by)\s+(?:on\s+)?(.+)", text)
        if expiry_match:
            item = expiry_match.group(1).strip().lstrip("the ")
            expires = expiry_match.group(2).strip()
            result = self.set_expiry(item, expires)
            ctx.tts_queue.put(_set_expiry_nlp_response(result))
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return True

        return False  # Not handled — pass to LLM with restricted tools

    # -----------------------------------------------------------------------
    # Catalog mode (inventory stocktake)
    # -----------------------------------------------------------------------

    def _enter_catalog_mode(self, text: str, ctx):
        """Enter catalog mode for a specific location."""
        # Extract location name from trigger text
        loc_text = text
        for trigger in self._CATALOG_TRIGGERS:
            if text.startswith(trigger):
                loc_text = text[len(trigger):].strip()
                break

        # Normalize word numbers: "freezer drawer three" → "freezer drawer 3"
        for word, num in _WORD_NUMBERS.items():
            loc_text = re.sub(rf'\b{word}\b', str(num), loc_text, flags=re.IGNORECASE)

        loc = self._find_location(loc_text) if loc_text else None
        if not loc:
            ctx.tts_queue.put(f"I don't know a location called {loc_text}. Try again with a specific location like fridge or freezer drawer 1.")
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True
            return

        # Get existing items in this location for reference
        existing_ids = {i["id"] for i in self._pantry["items"] if i.get("location_id") == loc["id"]}

        self._catalog_mode = {
            "location_id": loc["id"],
            "location_name": loc["name"],
            "mentioned": set(),
            "pre_existing": existing_ids,
            "added": 0,
            "updated": 0,
            "removed": 0,
        }
        self._shopping_mode_last_activity = datetime.now()

        # Show location contents
        self._publish_pantry_display(location_filter=loc["id"])
        self.event_system.publish(EventMessage(
            "status", "shopping_mode", {"mode": "catalog", "location": loc["name"]}
        ))
        existing_count = len(existing_ids)
        ctx.tts_queue.put(f"Cataloging the {loc['name']}. {existing_count} items currently listed. Call out what you see.")
        ctx.tts_queue.put("<EOS>")
        ctx.handled = True
        logger.info(f"[Pantry] Entered catalog mode for {loc['name']} ({existing_count} existing items)")

    def _handle_catalog_command(self, text: str, ctx):
        """Handle a single item callout in catalog mode."""
        ctx.handled = True
        loc_id = self._catalog_mode["location_id"]
        loc_name = self._catalog_mode["location_name"]

        # Check for removal: "no X" / "remove X" / "remove the X"
        remove_match = re.match(r"^(?:no|remove|remove the|none|take out|take out the)\s+(.+)$", text, re.IGNORECASE)
        if remove_match:
            item_name = remove_match.group(1).strip()
            removed = 0
            for item in list(self._pantry["items"]):
                if item.get("location_id") == loc_id and fuzz.partial_ratio(item_name, item["name"].lower()) >= 70:
                    self._pantry["items"].remove(item)
                    removed += 1
            if removed:
                self._catalog_mode["removed"] += removed
                self._save_pantry()
                self._publish_pantry_display(location_filter=loc_id)
                ctx.tts_queue.put(f"Removed {item_name}.")
            else:
                ctx.tts_queue.put(f"{item_name} not found.")
            ctx.tts_queue.put("<EOS>")
            return

        # Parse quantity + item: "5 eggs", "two chicken sausages", "eggs"
        quantity = None
        item_name = text.strip()

        # Check for leading word number: "two eggs", "three packs of butter"
        for word, num in _WORD_NUMBERS.items():
            pattern = rf'^{word}\s+(.+)$'
            m = re.match(pattern, item_name, re.IGNORECASE)
            if m:
                quantity = str(num)
                item_name = m.group(1).strip()
                break

        # Check for leading digit: "5 eggs", "12 cans"
        if not quantity:
            m = re.match(r'^(\d+)\s+(.+)$', item_name)
            if m:
                quantity = m.group(1)
                item_name = m.group(2).strip()

        # Handle "dozen" as multiplier: "2 dozen eggs" → quantity "24", item "eggs"
        if quantity:
            dozen_match = re.match(r'^dozen\s+(.+)$', item_name, re.IGNORECASE)
            if dozen_match:
                try:
                    quantity = str(int(quantity) * 12)
                except ValueError:
                    pass
                item_name = dozen_match.group(1).strip()
        elif item_name.lower().startswith("dozen "):
            quantity = "12"
            item_name = item_name[6:].strip()

        # Strip articles
        item_name = re.sub(r'^(?:a|an|some|the)\s+', '', item_name, flags=re.IGNORECASE).strip()

        if not item_name or len(item_name) < 2:
            return

        # Check for existing item in this location (strict matching to avoid false positives)
        existing = [i for i in self._pantry["items"]
                    if i.get("location_id") == loc_id
                    and fuzz.ratio(item_name, i["name"].lower()) >= 75]

        if existing:
            # Update existing item
            item = existing[0]
            if quantity:
                item["notes"] = quantity
            item["stored"] = datetime.now().isoformat(timespec="seconds")
            self._catalog_mode["mentioned"].add(item["id"])
            self._catalog_mode["updated"] += 1
            self._save_pantry()
            label = f"{item['name']}, {quantity}" if quantity else item["name"]
            ctx.tts_queue.put(label)
        else:
            # Add new item
            notes = quantity if quantity else None
            result = self.store_item(item=item_name, location=loc_name, notes=notes)
            if result.get("status") == "success":
                # Find the newly added item to track its ID
                new_items = [i for i in self._pantry["items"]
                             if i["name"].lower() == item_name.lower() and i.get("location_id") == loc_id]
                if new_items:
                    self._catalog_mode["mentioned"].add(new_items[-1]["id"])
                self._catalog_mode["added"] += 1
                label = f"Added {item_name}" + (f", {quantity}" if quantity else "")
                ctx.tts_queue.put(label)
            else:
                ctx.tts_queue.put(f"Could not add {item_name}")

        ctx.tts_queue.put("<EOS>")
        self._publish_pantry_display(location_filter=loc_id)

    def _exit_catalog_mode(self, ctx):
        """Exit catalog mode with reconciliation."""
        loc_id = self._catalog_mode["location_id"]
        loc_name = self._catalog_mode["location_name"]
        mentioned = self._catalog_mode["mentioned"]
        added = self._catalog_mode["added"]
        updated = self._catalog_mode["updated"]
        removed = self._catalog_mode["removed"]

        # Find items that existed before but weren't mentioned
        unmentioned = [
            i for i in self._pantry["items"]
            if i.get("location_id") == loc_id
            and i["id"] in self._catalog_mode["pre_existing"]
            and i["id"] not in mentioned
        ]

        if unmentioned:
            names = ", ".join(i["name"] for i in unmentioned[:5])
            extra = f" and {len(unmentioned) - 5} more" if len(unmentioned) > 5 else ""
            ctx.tts_queue.put(
                f"I still have {names}{extra} listed in the {loc_name} but you didn't mention them. "
                f"Say yes to remove them, or no to keep them."
            )
            ctx.tts_queue.put("<EOS>")
            ctx.handled = True

            # Store unmentioned items for follow-up yes/no handling
            self._catalog_mode["awaiting_reconciliation"] = True
            self._catalog_mode["unmentioned_ids"] = [i["id"] for i in unmentioned]
            return

        self._finish_catalog_exit(ctx, added, updated, removed, 0)

    def _finish_catalog_exit(self, ctx, added, updated, removed, reconciled):
        """Finalize catalog mode exit with summary."""
        loc_name = self._catalog_mode["location_name"]
        total = len([i for i in self._pantry["items"] if i.get("location_id") == self._catalog_mode["location_id"]])

        parts = []
        if added:
            parts.append(f"{added} added")
        if updated:
            parts.append(f"{updated} updated")
        if removed + reconciled:
            parts.append(f"{removed + reconciled} removed")

        summary = ", ".join(parts) if parts else "no changes"
        ctx.tts_queue.put(f"{loc_name} cataloged. {total} items total. {summary}.")
        ctx.tts_queue.put("<EOS>")
        ctx.handled = True

        self.event_system.publish(EventMessage(
            "status", "shopping_mode", {"mode": None}
        ))
        logger.info(f"[Pantry] Exited catalog mode for {loc_name}: {summary}")
        self._catalog_mode = None
        self._publish_pantry_display()
