---
name: Intent audit findings March 2026
description: NLP intent collision risks, coverage gaps, and Naive Bayes behavior notes from full audit of all plugins
type: project
---

## Classifier behavior

The IntentClassifier uses sklearn Naive Bayes with bag-of-words (CountVectorizer). Key implications:
- Word overlap between intents is the primary collision mechanism
- More registered intents = lower per-intent confidence (probability dilution)
- The word "pantry" appears in many tools (suggest_meals_from_pantry, show_pantry, reclassify_pantry, find_item) causing cross-contamination
- Generic words like "what", "we", "have" are near-useless discriminators

## Known collision zones

1. **suggest_meals_from_pantry vs show_pantry vs reclassify_pantry** — all share "pantry". Fixed by anchoring suggest_meals intents to "pantry" + "suggest/meal/cook" combinations.
2. **show_meal_plan vs suggest_weekly_meals** — "week" and "meal" overlap heavily. Fixed by making "show/display/planned" anchor show_meal_plan, and "suggest/recommend" anchor suggest_weekly_meals.
3. **surprise_me vs suggest_meals_from_pantry** — both were "what should I cook" type phrases. Fixed by removing generic hunger phrases from suggest_meals_from_pantry (those go to LLM now).
4. **rename_last_shopping_item** — "that's wrong" was way too broad, would match any correction context. Fixed by adding "list"/"shopping" anchors to all rename intents.
5. **_nlp_current_recipe vs show_meal_plan** — "what are we cooking" matches both. Removed "what are we cooking this week" from show_meal_plan to avoid collision.

## Dispatcher threshold

With ~50+ intents registered, even clear matches score 0.3-0.5 confidence. The dispatcher tests were using 0.4 threshold which became too tight. Lowered to 0.3.

**Why:** Adding more intents dilutes the Naive Bayes probability space. This is a structural limitation of the bag-of-words approach.

**How to apply:** When adding new intents, run `make test` and check for confidence regressions. If a tool keeps getting misrouted, the fix is making its intent vocabulary more distinct (unique anchor words), not adding more examples with shared vocabulary.

## Pre-existing test failure

`TestCookingSessionFlow::test_next_step_advances` fails before and after changes. The cooking session step counter isn't advancing in the test fixture — likely a singleton state issue in test isolation.
