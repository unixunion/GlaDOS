from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParamaterType
from plugins.plugin_manager import PluginManager
from loguru import logger

plugin_manager = PluginManager()

subtract_two_numbers_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='subtract_two_numbers',
                        description="Subtract two numbers",
                        parameters=Parameters(type="object", required=['a', 'b'], properties={
                            'a': ParamaterType(type="integer", description="the first number"),
                            'b': ParamaterType(type="integer", description="the second number")
                        })
                    )
                    )
)


@plugin_manager.register(
    "subtract_two_numbers",
    "subtracts a number from another",
    subtract_two_numbers_definition.to_dict()
)
def subtract_two_numbers(a: int, b: int) -> int:
    """
  Subtract two numbers
  """
    return a - b




add_two_numbers_definition = (
    FunctionRequest(type="function",
                    function=FunctionMetadata(
                        name='add_two_numbers',
                        description="Add two numbers",
                        parameters=Parameters(type="object", required=['a', 'b'], properties={
                            'a': ParamaterType(type="integer", description="the first number"),
                            'b': ParamaterType(type="integer", description="the second number")
                        })
                    )
                    )
)

@plugin_manager.register(
    "add_two_numbers",
    "add two numbers together",
    add_two_numbers_definition.to_dict()
)
def add_two_numbers(a: int, b: int) -> int:
    """
    Add two numbers
    """
    logger.info(f"Adding two numbers together: {a} and {b}")
    return a + b
