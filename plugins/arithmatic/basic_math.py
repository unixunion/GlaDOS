
from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from plugins.plugin_manager import PluginManager
from loguru import logger

plugin_manager = PluginManager()

subtract_two_numbers_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='subtract_two_numbers',
                        description="Subtract two numbers",
                        parameters=Parameters(type="object", required=['a', 'b'], properties={
                            'a': ParameterType(type="integer", description="the first number"),
                            'b': ParameterType(type="integer", description="the second number")
                        })
                    )
                    )
)


@plugin_manager.register(
    "subtract_two_numbers",
    "subtracts a number from another",
    subtract_two_numbers_definition.to_dict(),
    process_output=False
)
def subtract_two_numbers(a: int, b: int) -> int:
    """
  Subtract two numbers
  """
    logger.info(f"Subtracting {b} from {a}")
    return int(a) - int(b)




add_two_numbers_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='add_two_numbers',
                        description="Add two numbers",
                        parameters=Parameters(type="object", required=['a', 'b'], properties={
                            'a': ParameterType(type="integer", description="the first number"),
                            'b': ParameterType(type="integer", description="the second number")
                        })
                    )
                    )
)

@plugin_manager.register(
    "add_two_numbers",
    "add two numbers together",
    add_two_numbers_definition.to_dict(),
    process_output=False
)
def add_two_numbers(a: int, b: int) -> int:
    """
    Add two numbers
    """
    logger.info(f"Adding two numbers together: {a} and {b}")
    return int(a) + int(b)
