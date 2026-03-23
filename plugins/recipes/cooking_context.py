"""Cooking-context NLP commands — read ingredients, step through directions, etc.

These are NLP-only handlers: they don't call external tools, they read from the
NLPDispatcher session state that was populated when `select_recipe` was called.
They register with the IntentClassifier so they're routable, and with the
NLPHandlerRegistry so the dispatcher knows how to handle them.

All commands are scoped to Activity.COOKING so they only appear when the user
is in cooking mode.
"""

from loguru import logger

from glados.context.activity import Activity
from glados.nlp.handler import NLPHandler, NLPHandlerRegistry
from glados.system.intent_classifier import IntentClassifier

_registry = NLPHandlerRegistry()


def _get_cooking_session() -> dict:
    """Get the current cooking session, or empty dict."""
    dispatcher = _registry.dispatcher
    if dispatcher is None:
        return {}
    return dispatcher.get_session(Activity.COOKING)


def _get_recipe() -> dict | None:
    """Get the selected recipe from session, or None."""
    session = _get_cooking_session()
    return session.get("selected_recipe")


def _get_steps() -> list[str]:
    """Get parsed step list from the selected recipe, or empty list."""
    recipe = _get_recipe()
    if not recipe:
        return []
    directions = recipe.get("directions", "")
    if not directions:
        return []
    return [s.strip() for s in directions.split("\n") if s.strip()]


# ---------------------------------------------------------------------------
# Intent examples — more examples = better Naive Bayes accuracy.
# Include casual/short forms that real users say while cooking.
# ---------------------------------------------------------------------------

COOKING_INTENTS = [
    {
        "name": "_nlp_list_ingredients",
        "examples": [
            "list the ingredients",
            "list ingredients",
            "ingredients",
            "whats the ingredients",
            "what ingredients do I need",
            "read the ingredients",
            "what are the ingredients",
            "tell me the ingredients",
            "ingredients please",
            "what do I need for this recipe",
            "read me the ingredients",
            "show me the ingredients",
            "give me the ingredient list",
            "what goes in this",
            "what do I need",
            "read ingredients",
        ],
    },
    {
        "name": "_nlp_list_steps",
        "examples": [
            "what are the steps",
            "read the directions",
            "list the steps",
            "what are the directions",
            "tell me the steps",
            "how do I make this",
            "read the instructions",
            "what do I do",
            "steps",
            "directions",
            "instructions",
            "show me the steps",
            "read me the steps",
            "list all steps",
            "go through the steps",
            "walk me through it",
            "tell me the directions",
            "how do I cook this",
            "go step by step",
            "step by step",
            "read all the steps",
            "give me the steps",
        ],
    },
    {
        "name": "_nlp_next_step",
        "examples": [
            "next step",
            "what's next",
            "continue",
            "and then",
            "what do I do next",
            "move on",
            "okay next step",
            "ready for the next step",
            "then what",
            "keep going",
            "okay done what's the next step",
            "done with this step",
            "okay what now",
            "what comes next",
            "next step please",
            "carry on",
            "now what",
            "proceed to the next step",
        ],
    },
    {
        "name": "_nlp_previous_step",
        "examples": [
            "previous step",
            "previous step please",
            "go back",
            "go back one",
            "the one before",
            "before that",
            "what was the previous one",
            "go to the previous one",
            "back up",
            "read the previous one",
            "take me back",
            "go backwards",
            "back one step",
        ],
    },
    {
        "name": "_nlp_repeat_step",
        "examples": [
            "repeat that",
            "say that again",
            "what was that step",
            "repeat the step",
            "can you repeat that",
            "what did you say",
            "again please",
            "repeat",
            "again",
            "one more time",
            "say it again",
            "come again",
            "tell me again",
            "read that again",
            "sorry what",
            "I didn't catch that",
        ],
    },
    {
        "name": "_nlp_first_step",
        "examples": [
            "first step",
            "start from the beginning",
            "go to the first step",
            "start over",
            "restart the steps",
            "back to the start",
            "from the top",
            "beginning",
            "go to step one",
            "start from step one",
            "reset the steps",
        ],
    },
    {
        "name": "_nlp_current_recipe",
        "examples": [
            "what recipe is selected",
            "what are we making",
            "what are we cooking",
            "which recipe",
            "what recipe is this",
            "what am I making",
            "what recipe did I pick",
            "what did I select",
            "which recipe is loaded",
            "what dish are we making",
        ],
    },
]


# ---------------------------------------------------------------------------
# Response formatters (used as NLPHandler.response_fn)
# ---------------------------------------------------------------------------

def _list_ingredients_response(_result) -> str:
    recipe = _get_recipe()
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."
    ingredients = recipe.get("ingredients", "")
    if not ingredients:
        return "I don't have ingredients for this recipe."
    title = recipe.get("title", "the recipe")
    return f"The ingredients for {title} are: {ingredients}"


def _list_steps_response(_result) -> str:
    recipe = _get_recipe()
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."
    directions = recipe.get("directions", "")
    if not directions:
        return "I don't have directions for this recipe."
    title = recipe.get("title", "the recipe")
    return f"The steps for {title} are: {directions}"


def _next_step_response(_result) -> str:
    session = _get_cooking_session()
    recipe = session.get("selected_recipe")
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."

    steps = _get_steps()
    if not steps:
        return "I don't have directions for this recipe."

    current = session.get("current_step", 0)

    if current >= len(steps):
        return "That's all the steps! You're done."

    step_text = steps[current]
    session["current_step"] = current + 1

    if current + 1 >= len(steps):
        return f"{step_text}. That's the last step!"
    return step_text


def _previous_step_response(_result) -> str:
    session = _get_cooking_session()
    recipe = session.get("selected_recipe")
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."

    steps = _get_steps()
    if not steps:
        return "I don't have directions for this recipe."

    current = session.get("current_step", 0)

    # current_step points to the *next* unread step.
    # Go back two: one to reach the step we just read, one more to the previous.
    target = max(0, current - 2)
    session["current_step"] = target + 1

    return steps[target]


def _repeat_step_response(_result) -> str:
    session = _get_cooking_session()
    recipe = session.get("selected_recipe")
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."

    steps = _get_steps()
    if not steps:
        return "I don't have directions for this recipe."

    current = session.get("current_step", 0)

    # Repeat the previous step (current_step points to the *next* one)
    repeat_idx = max(0, current - 1)
    if repeat_idx >= len(steps):
        return "There are no steps to repeat."
    return steps[repeat_idx]


def _first_step_response(_result) -> str:
    session = _get_cooking_session()
    recipe = session.get("selected_recipe")
    if not recipe:
        return "No recipe is selected. Search for a recipe and select one first."

    steps = _get_steps()
    if not steps:
        return "I don't have directions for this recipe."

    session["current_step"] = 1
    return steps[0]


def _current_recipe_response(_result) -> str:
    recipe = _get_recipe()
    if not recipe:
        return "No recipe is currently selected."
    title = recipe.get("title", "Unknown")
    return f"We're making {title}."


# ---------------------------------------------------------------------------
# Handler map: intent name -> response function
# ---------------------------------------------------------------------------

_HANDLER_MAP = {
    "_nlp_list_ingredients": _list_ingredients_response,
    "_nlp_list_steps": _list_steps_response,
    "_nlp_next_step": _next_step_response,
    "_nlp_previous_step": _previous_step_response,
    "_nlp_repeat_step": _repeat_step_response,
    "_nlp_first_step": _first_step_response,
    "_nlp_current_recipe": _current_recipe_response,
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_cooking_intents():
    """Register cooking NLP intents with the classifier and handler registry."""
    classifier = IntentClassifier()
    registry = NLPHandlerRegistry()

    for intent in COOKING_INTENTS:
        classifier.add_intent(intent["name"], intent["examples"])

    classifier.retrain()
    logger.info(f"[NLP Cooking] Registered {len(COOKING_INTENTS)} cooking context intents")

    for intent_name, response_fn in _HANDLER_MAP.items():
        registry.register(NLPHandler(
            tool_name=intent_name,
            extract_fn=lambda text: {},  # No params needed — reads from session
            response_fn=response_fn,
            activity=[Activity.COOKING],
        ))
    logger.info(f"[NLP Cooking] Registered {len(_HANDLER_MAP)} cooking context handlers")


# Auto-register when the module is imported (discovered by load_plugins)
register_cooking_intents()
