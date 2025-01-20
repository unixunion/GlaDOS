import dataclasses
from typing import Union, List, Dict, Any, Optional
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
class ParameterType:
    type: str  # Allow for "string" or ["string", "null"] etc.
    description: Optional[str] = None  # Description is optional
    enum: Optional[List[Any]] = None  # For enumerated values
    # properties: Optional[Dict[str, "ParameterType"]] = None  # Nested properties for object types
    # required: Optional[List[str]] = None  # Required fields for objects
    # additionalProperties: Optional[bool] = True  # For strict mode


@dataclasses.dataclass
class Parameters:
    type: str  # Typically "object"
    properties: Dict[str, ParameterType]
    required: Optional[List[str]] = None
    additionalProperties: Optional[bool] = False  # Allows/disallows extra fields


@dataclasses.dataclass
class FunctionMetadata:
    name: str
    description: str
    parameters: Parameters


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class FunctionRequest:
    type: str
    function: FunctionMetadata


# @dataclasses.dataclass
# class ParamaterType:
#     type: str
#     description: str
#
#
# @dataclasses.dataclass
# class Parameters:
#     type: str
#     required: list
#     properties: dict
#
# @dataclasses.dataclass
# class FunctionMetadata:
#     name: str
#     description: str
#     parameters: object
#
#
# @dataclasses_json.dataclass_json
# @dataclasses.dataclass
# class FunctionRequest:
#     foo = "bar"
#     type: str
#     function: object


