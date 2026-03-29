import re

import requests
from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool

# Open-Meteo API — free, no API key required
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → spoken descriptions
_WMO_DESCRIPTIONS = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _geocode(location: str) -> dict | None:
    """Convert a location name to lat/lon using Open-Meteo geocoding."""
    try:
        resp = requests.get(_GEOCODE_URL, params={"name": location, "count": 1}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0]
    except Exception as e:
        logger.warning(f"[Weather] Geocoding failed for '{location}': {e}")
    return None


def _get_weather(lat: float, lon: float) -> dict | None:
    """Fetch current weather from Open-Meteo."""
    try:
        resp = requests.get(_WEATHER_URL, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
        }, timeout=5)
        resp.raise_for_status()
        return resp.json().get("current")
    except Exception as e:
        logger.warning(f"[Weather] API request failed: {e}")
    return None


def _format_weather_response(result: str) -> str:
    return result


@mcp_tool(
    description="Get current weather for a location.",
    parameters={
        "location": {
            "type": "string",
            "description": "The location for which to check the weather conditions",
        },
    },
    required=["location"],
    intents=[
        "what is the weather",
        "what is the weather like",
        "what is the weather outside",
        "what's it like outside",
        "what is the temperature",
        "what is the temperature outside",
        "tell me the weather conditions",
        "tell me the weather",
        "is it cold today",
        "is it hot outside",
        "what will the weather be like",
        "when will it rain",
        "is it going to snow tomorrow",
        "weather forecast",
        "how is the weather",
        "weather in london",
        "weather report",
    ],
    process_output=True,
    activity=[Activity.GENERAL, Activity.UTILITIES],
    nlp_extractors={
        "location": [
            re.compile(r"\b(?:in|for|at)\s+(?P<location>.+?)(?:\s*[?.!]?\s*)$", re.IGNORECASE),
        ],
    },
    nlp_response=_format_weather_response,
)
def handle_weather(location: str = "") -> str:
    if not location:
        return "I need a location. Try saying weather in followed by the city name."

    geo = _geocode(location)
    if not geo:
        return f"I couldn't find {location}. Try a different city name."

    city = geo.get("name", location)
    country = geo.get("country", "")
    lat, lon = geo["latitude"], geo["longitude"]

    current = _get_weather(lat, lon)
    if not current:
        return f"I couldn't get weather data for {city}."

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")
    code = current.get("weather_code", 0)
    condition = _WMO_DESCRIPTIONS.get(code, "unknown conditions")

    parts = [f"In {city}"]
    if country:
        parts[0] += f", {country}"
    parts.append(f"it's currently {temp} degrees celsius with {condition}")
    if feels is not None and abs(feels - temp) >= 2:
        parts.append(f"Feels like {feels} degrees")
    if humidity is not None:
        parts.append(f"Humidity is {humidity} percent")
    if wind is not None:
        parts.append(f"Wind speed is {wind} kilometers per hour")

    logger.success(f"[Weather] {city}: {temp}°C, {condition}")
    return ". ".join(parts) + "."
