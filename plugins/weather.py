from plugins.plugin_manager import PluginManager
from loguru import logger

plugin_manager = PluginManager()

@plugin_manager.register(
    "weather",
    "get the current weather data",
    function_request={
        "query": {"type": "str", "query": "The place and or timespan to search weather data for"}
    }
)
def handle_weather(location: str) -> str:
    logger.success("Handling weather")
    weather_data = {
        "new york": "Sunny, 25°C",
        "london": "Rainy, 15°C",
    }
    return weather_data.get(location.lower(), f"No weather data for {location}.")
