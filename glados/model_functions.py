import dataclasses

import dataclasses_json

"""
Usage example:
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
                  ))

"""


@dataclasses.dataclass
class ParamaterType:
    type: str
    description: str


@dataclasses.dataclass
class Parameters:
    type: str
    required: list
    properties: dict

@dataclasses.dataclass
class FunctionMetadata:
    name: str
    description: str
    parameters: object


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class FunctionRequest:
    foo = "bar"
    type: str
    function: object


