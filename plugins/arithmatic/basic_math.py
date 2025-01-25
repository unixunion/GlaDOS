from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                                         function=FunctionMetadata(
                                             description="Subtract a number from another",
                                             parameters=Parameters(type="object", required=['a', 'b'], properties={
                                                 'a': ParameterType(type="integer", description="the first number"),
                                                 'b': ParameterType(type="integer", description="the second number")
                                             })
                                         )
                                         ),
    intents=[
        "What is 56 minus 12",
        "Subtract six from one hundred and sixty seven",
        "What is nineteen subtract five",
        "What is fifty two minus seventeen",
    ],
    process_output=False,
    activity=[Activity.UTILITIES, Activity.COOKING]
)
def subtract_two_numbers(a: int, b: int) -> int:
    """
    Subtracts two numbers
    """
    logger.info(f"Subtracting {b} from {a}")
    return int(a) - int(b)


add_two_numbers_definition = (

)


@plugin_manager.register(
    llm_function_request=FunctionRequest(type="function",
                    function=FunctionMetadata(
                        description="Add two numbers together",
                        parameters=Parameters(type="object", required=['a', 'b'], properties={
                            'a': ParameterType(type="integer", description="the first number"),
                            'b': ParameterType(type="integer", description="the second number")
                        })
                    )
                    ),
    intents=[
            "What is 5 plus 7",
            "Add nine and four together",
            "What is the sum of seven and one hundred and sixty two",
            "Please add 67 and 141",
        ],
    process_output=False,
    activity=[Activity.UTILITIES, Activity.COOKING]
)
def add_two_numbers(a: int, b: int) -> int:
    """
    Add two numbers
    """
    logger.info(f"Adding two numbers together: {a} and {b}")
    return int(a) + int(b)
