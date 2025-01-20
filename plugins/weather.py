from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.plugin_manager import PluginManager
from loguru import logger

plugin_manager = PluginManager()

@plugin_manager.register(
    "handle_weather",
    "get the current weather data",
    FunctionRequest(
        type="function",
        function=FunctionMetadata(
            name="handle_weather",
            description="Retrieve weather for a location.",
            parameters=Parameters(
                type="object",
                properties={
                    "location": ParameterType(
                        type="string",
                        description="The location for which to check the weather conditions"
                    ),
                },
                required=["location"],
                additionalProperties=False
            )
        )).to_dict(),
    process_output=False,
)
def handle_weather(location: str) -> str:
    logger.success("Handling weather")
    weather_data = {
        "new york": "Sunny, 25°C",
        "london": "Rainy, 15°C",
    }
    return weather_data.get(location.lower(), f"No weather data for {location}.")
