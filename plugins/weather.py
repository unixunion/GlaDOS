from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.plugin_system.plugin_manager import PluginManager
from loguru import logger

plugin_manager = PluginManager()


@plugin_manager.register(
    llm_function_request=FunctionRequest(
        type="function",
        function=FunctionMetadata(
            description="Get current weather for a location.",
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
        )),
    intents=[
        "what is the weather",
        "what's it like outside",
        "what is the temperature",
        "tell me the weather conditions",
        "is it cold today?"
        "what will the weather be like",
        "when will it rain",
        "is it going to snow tomorrow"
    ],
    process_output=False,
)
def handle_weather(location: str) -> str:
    logger.success("Handling weather")
    weather_data = {
        "new york": "Sunny, 25°C",
        "london": "Rainy, 15°C",
    }
    return weather_data.get(location.lower(), f"No weather data for {location}.")
