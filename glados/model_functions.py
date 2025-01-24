import dataclasses
from typing import List, Dict, Any, Optional

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


@dataclasses.dataclass
class Parameters:
    type: str  # Typically "object"
    properties: Dict[str, ParameterType]
    required: Optional[List[str]] = None
    additionalProperties: Optional[bool] = False  # Allows/disallows extra fields


@dataclasses.dataclass
class FunctionMetadata:
    description: str
    parameters: Parameters
    name: str = None


@dataclasses_json.dataclass_json
@dataclasses.dataclass
class FunctionRequest:
    function: FunctionMetadata
    type: str = "function"


@dataclasses.dataclass
class FunctionIntents:
    examples: List[str] = dataclasses.field(default_factory=list)
