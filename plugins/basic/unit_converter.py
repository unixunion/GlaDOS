"""Unit conversion plugin — temperature, weight, volume, length."""

import re

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool

# ---------------------------------------------------------------------------
# Conversion tables
# ---------------------------------------------------------------------------

# Each entry: (from_unit, to_unit) -> (multiply, add)
# result = value * multiply + add
_CONVERSIONS: dict[tuple[str, str], tuple[float, float]] = {
    # Temperature
    ("fahrenheit", "celsius"):    (5 / 9, -32 * 5 / 9),
    ("celsius", "fahrenheit"):    (9 / 5, 32),
    ("celsius", "kelvin"):        (1, 273.15),
    ("kelvin", "celsius"):        (1, -273.15),
    ("fahrenheit", "kelvin"):     (5 / 9, (-32 * 5 / 9) + 273.15),
    ("kelvin", "fahrenheit"):     (9 / 5, -459.67),
    # Weight / mass
    ("pounds", "grams"):          (453.592, 0),
    ("grams", "pounds"):          (1 / 453.592, 0),
    ("pounds", "kilograms"):      (0.453592, 0),
    ("kilograms", "pounds"):      (2.20462, 0),
    ("ounces", "grams"):          (28.3495, 0),
    ("grams", "ounces"):          (1 / 28.3495, 0),
    ("kilograms", "grams"):       (1000, 0),
    ("grams", "kilograms"):       (0.001, 0),
    ("ounces", "pounds"):         (1 / 16, 0),
    ("pounds", "ounces"):         (16, 0),
    ("stones", "pounds"):         (14, 0),
    ("pounds", "stones"):         (1 / 14, 0),
    ("stones", "kilograms"):      (6.35029, 0),
    ("kilograms", "stones"):      (1 / 6.35029, 0),
    # Volume
    ("cups", "milliliters"):      (236.588, 0),
    ("milliliters", "cups"):      (1 / 236.588, 0),
    ("liters", "gallons"):        (0.264172, 0),
    ("gallons", "liters"):        (3.78541, 0),
    ("tablespoons", "milliliters"): (14.787, 0),
    ("milliliters", "tablespoons"): (1 / 14.787, 0),
    ("teaspoons", "milliliters"): (4.929, 0),
    ("milliliters", "teaspoons"): (1 / 4.929, 0),
    ("cups", "liters"):           (0.236588, 0),
    ("liters", "cups"):           (1 / 0.236588, 0),
    ("pints", "liters"):          (0.473176, 0),
    ("liters", "pints"):          (1 / 0.473176, 0),
    ("quarts", "liters"):         (0.946353, 0),
    ("liters", "quarts"):         (1 / 0.946353, 0),
    ("fluid ounces", "milliliters"): (29.5735, 0),
    ("milliliters", "fluid ounces"): (1 / 29.5735, 0),
    # Length / distance
    ("inches", "centimeters"):    (2.54, 0),
    ("centimeters", "inches"):    (1 / 2.54, 0),
    ("feet", "meters"):           (0.3048, 0),
    ("meters", "feet"):           (1 / 0.3048, 0),
    ("miles", "kilometers"):      (1.60934, 0),
    ("kilometers", "miles"):      (1 / 1.60934, 0),
    ("yards", "meters"):          (0.9144, 0),
    ("meters", "yards"):          (1 / 0.9144, 0),
}

# Aliases: map common short/alternate forms to canonical unit names
_ALIASES: dict[str, str] = {
    "f": "fahrenheit", "fahr": "fahrenheit",
    "c": "celsius", "centigrade": "celsius",
    "k": "kelvin",
    "lb": "pounds", "lbs": "pounds", "pound": "pounds",
    "g": "grams", "gram": "grams",
    "kg": "kilograms", "kilogram": "kilograms", "kilo": "kilograms", "kilos": "kilograms",
    "oz": "ounces", "ounce": "ounces",
    "stone": "stones", "st": "stones",
    "cup": "cups",
    "ml": "milliliters", "milliliter": "milliliters", "mls": "milliliters",
    "l": "liters", "liter": "liters", "litre": "liters", "litres": "liters",
    "tbsp": "tablespoons", "tablespoon": "tablespoons",
    "tsp": "teaspoons", "teaspoon": "teaspoons",
    "gal": "gallons", "gallon": "gallons",
    "pt": "pints", "pint": "pints",
    "qt": "quarts", "quart": "quarts",
    "fl oz": "fluid ounces", "fluid ounce": "fluid ounces",
    "in": "inches", "inch": "inches",
    "cm": "centimeters", "centimeter": "centimeters", "cms": "centimeters",
    "ft": "feet", "foot": "feet",
    "m": "meters", "meter": "meters", "metre": "meters", "metres": "meters",
    "mi": "miles", "mile": "miles",
    "km": "kilometers", "kilometer": "kilometers", "kilometre": "kilometers", "kilometres": "kilometers",
    "yd": "yards", "yard": "yards",
}


def _normalize_unit(raw: str) -> str:
    """Normalize a unit string to its canonical form."""
    raw = raw.strip().lower()
    return _ALIASES.get(raw, raw)


def _convert(value: float, from_unit: str, to_unit: str) -> float | None:
    """Perform the conversion. Returns None if the pair is unknown."""
    key = (from_unit, to_unit)
    if key not in _CONVERSIONS:
        return None
    mult, add = _CONVERSIONS[key]
    return value * mult + add


# ---------------------------------------------------------------------------
# NLP extraction
# ---------------------------------------------------------------------------

# Patterns for "convert 100 fahrenheit to celsius", "100f to c",
# "how many grams in 2 pounds", "what is 5 miles in km"
_CONVERT_PATTERNS = [
    # "convert 100 fahrenheit to celsius" / "100 fahrenheit in celsius"
    re.compile(
        r"(?:convert\s+)?(?P<value>[\d.]+)\s*(?P<from>.+?)\s+(?:to|in|into)\s+(?P<to>.+?)$",
        re.IGNORECASE,
    ),
    # "how many grams in 2 pounds"
    re.compile(
        r"how\s+(?:many|much)\s+(?P<to>.+?)\s+(?:in|are in)\s+(?P<value>[\d.]+)\s*(?P<from>.+?)$",
        re.IGNORECASE,
    ),
    # "what is 32 f in c"
    re.compile(
        r"what(?:'s| is)\s+(?P<value>[\d.]+)\s*(?P<from>.+?)\s+in\s+(?P<to>.+?)$",
        re.IGNORECASE,
    ),
]


def _extract_conversion(text: str) -> dict:
    """Extract value, from_unit, and to_unit from natural language."""
    for pattern in _CONVERT_PATTERNS:
        m = pattern.search(text)
        if m:
            return {
                "value": float(m.group("value")),
                "from_unit": m.group("from").strip(),
                "to_unit": m.group("to").strip(),
            }
    return {}


def _format_conversion_response(result) -> str:
    """Format conversion result for TTS."""
    if isinstance(result, dict):
        if result.get("error"):
            return result["error"]
        return result.get("message", "Done.")
    return str(result)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

@mcp_tool(
    description=(
        "Convert a value from one unit to another. "
        "Supports temperature (fahrenheit, celsius, kelvin), "
        "weight (pounds, grams, kilograms, ounces, stones), "
        "volume (cups, liters, milliliters, tablespoons, teaspoons, gallons, pints, quarts), "
        "and length (inches, centimeters, feet, meters, miles, kilometers)."
    ),
    parameters={
        "value": {
            "type": "number",
            "description": "The numeric value to convert",
        },
        "from_unit": {
            "type": "string",
            "description": "The unit to convert from (e.g. fahrenheit, pounds, cups, miles)",
        },
        "to_unit": {
            "type": "string",
            "description": "The unit to convert to (e.g. celsius, grams, liters, kilometers)",
        },
    },
    required=["value", "from_unit", "to_unit"],
    intents=[
        "convert 100 fahrenheit to celsius",
        "what is 200 degrees fahrenheit in celsius",
        "how many grams in 2 pounds",
        "convert 5 miles to kilometers",
        "what is 1 cup in milliliters",
        "convert 32 fahrenheit to celsius",
        "how many pounds is 1 kilogram",
        "convert 500 grams to pounds",
        "what is 6 feet in meters",
        "convert 10 ounces to grams",
        "how many cups in a liter",
        "what is 350 f in c",
        "convert 2 tablespoons to milliliters",
        "how many liters in a gallon",
        "convert 70 kilograms to stones",
    ],
    process_output=True,
    activity=[Activity.GENERAL, Activity.UTILITIES, Activity.COOKING],
    nlp_extract_fn=_extract_conversion,
    nlp_response=_format_conversion_response,
)
def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """Convert a value between units."""
    from_norm = _normalize_unit(from_unit)
    to_norm = _normalize_unit(to_unit)

    if from_norm == to_norm:
        return {"message": f"{value} {from_norm} is, well, {value} {to_norm}."}

    result = _convert(value, from_norm, to_norm)
    if result is None:
        return {"error": f"I don't know how to convert {from_norm} to {to_norm}."}

    # Format nicely: no unnecessary decimals
    if result == int(result):
        formatted = str(int(result))
    else:
        formatted = f"{result:.2f}"

    logger.info(f"[UnitConverter] {value} {from_norm} = {formatted} {to_norm}")
    return {"message": f"{value} {from_norm} is {formatted} {to_norm}."}
