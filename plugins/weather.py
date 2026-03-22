from loguru import logger

from glados.context.activity import Activity
from glados.mcp.decorators import mcp_tool


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
        "what's it like outside",
        "what is the temperature",
        "tell me the weather conditions",
        "is it cold today?",
        "what will the weather be like",
        "when will it rain",
        "is it going to snow tomorrow"
    ],
    process_output=True,
    activity=[Activity.GENERAL, Activity.UTILITIES],
)
def handle_weather(location: str) -> str:
    logger.success("Handling weather")
    weather_data = {
        "new york": "Sunny, 25°C",
        "london": "Rainy, 15°C",
    }
    return weather_data.get(location.lower(), f"No weather data for {location}.")
