from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem
from loguru import logger

plugin_manager = PluginSystem()


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
