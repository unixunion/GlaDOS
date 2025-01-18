import asyncio
import dataclasses

import dataclasses_json
import ollama
from ollama import ChatResponse

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters, ParamaterType


# class EnhancedJSONEncoder(json.JSONEncoder):
#     def default(self, o):
#         if dataclasses.is_dataclass(o):
#             return dataclasses.asdict(o)
#         return super().default(o)


def add_two_numbers(a: int, b: int) -> int:
    """
  Add two numbers

  Args:
    a (int): The first number
    b (int): The second number

  Returns:
    int: The sum of the two numbers
  """
    return a + b


def subtract_two_numbers(a: int, b: int) -> int:
    """
  Subtract two numbers
  """
    return a - b


# @dataclasses_json.dataclass_json
# @dataclasses.dataclass
# class ParamaterType:
#     type: str
#     description: str
#
#
# # @dataclasses_json.dataclass_json
# @dataclasses.dataclass
# class Parameters:
#     type: str
#     required: list
#     properties: dict
#
#
# # @dataclasses_json.dataclass_json
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
#

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

print(subtract_two_numbers_definition.to_json())

# exit(0)

# Tools can still be manually defined and passed into chat
# subtract_two_numbers_tool = {
#   'type': 'function',
#   'function': {
#     'name': 'subtract_two_numbers',
#     'description': 'Subtract two numbers',
#     'parameters': {
#       'type': 'object',
#       'required': ['a', 'b'],
#       'properties': {
#         'a': {'type': 'integer', 'description': 'The first number'},
#         'b': {'type': 'integer', 'description': 'The second number'},
#       },
#     },
#   },
# }

messages = [{'role': 'user', 'content': 'What is three plus one?'}]
print('Prompt:', messages[0]['content'])

available_functions = {
    'add_two_numbers': add_two_numbers,
    'subtract_two_numbers': subtract_two_numbers,
}


async def main():
    client = ollama.AsyncClient()

    response: ChatResponse = await client.chat(
        'llama3.1',
        messages=messages,
        # a function or the definition
        tools=[add_two_numbers, subtract_two_numbers_definition.to_dict()],
    )

    if response.message.tool_calls:
        # There may be multiple tool calls in the response
        for tool in response.message.tool_calls:
            # Ensure the function is available, and then call it
            if function_to_call := available_functions.get(tool.function.name):
                print('Calling function:', tool.function.name)
                print('Arguments:', tool.function.arguments)
                output = function_to_call(**tool.function.arguments)
                print('Function output:', output)
            else:
                print('Function', tool.function.name, 'not found')

    # Only needed to chat with the model using the tool call results
    if response.message.tool_calls:
        # Add the function response to messages for the model to use
        messages.append(response.message)
        messages.append({'role': 'tool', 'content': str(output), 'name': tool.function.name})

        # Get final response from model with function outputs
        final_response = await client.chat('llama3.1', messages=messages)
        print('Final response:', final_response.message.content)

    else:
        print('No tool calls returned from model')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nGoodbye!')

# import json
# import pprint
# from llamaapi import LlamaAPI
#
# # Initialize the SDK
# # http://localhost:11434/api/chat
# llama = LlamaAPI(hostname="http://localhost:11434", domain_path="/api/chat", api_token="Bearer your_api_key_here")

# Build the API request
# api_request_json = {
#     "model": "llama3.1",
#     # "model": "hf.co/mradermacher/Llama3-8B-function-calling-uncensored-i1-GGUF:Q5_K_M",
#     # "model": "hf.co/mradermacher/Llama3-8B-function-calling-uncensored-i1-GGUF:Q4_K_M",
#     "messages": [
#         {
#             "role": "function_metadata",
#             "content": "[\r\n    {\r\n        \"type\": \"function\",\r\n        \"function\": {\r\n            \"name\": \"get_current_weather\",\r\n            \"description\": \"This function gets the current weather in a given city\",\r\n            \"parameters\": {\r\n                \"type\": \"object\",\r\n                \"properties\": {\r\n                    \"city\": {\r\n                        \"type\": \"string\",\r\n                        \"description\": \"The city, e.g., San Francisco\"\r\n                    },\r\n                    \"format\": {\r\n                        \"type\": \"string\",\r\n                        \"enum\": [\"celsius\", \"fahrenheit\"],\r\n                        \"description\": \"The temperature unit to use.\"\r\n                    }\r\n                },\r\n                \"required\": [\"city\"]\r\n            }\r\n        }\r\n    },\r\n    {\r\n        \"type\": \"function\",\r\n        \"function\": {\r\n            \"name\": \"get_clothes\",\r\n            \"description\": \"This function provides a suggestion of clothes to wear based on the current weather\",\r\n            \"parameters\": {\r\n                \"type\": \"object\",\r\n                \"properties\": {\r\n                    \"temperature\": {\r\n                        \"type\": \"string\",\r\n                        \"description\": \"The temperature, e.g., 15 C or 59 F\"\r\n                    },\r\n                    \"condition\": {\r\n                        \"type\": \"string\",\r\n                        \"description\": \"The weather condition, e.g., 'Cloudy', 'Sunny', 'Rainy'\"\r\n                    }\r\n                },\r\n                \"required\": [\"temperature\", \"condition\"]\r\n            }\r\n        }\r\n    }    \r\n]\r\n"
#         },
#         {
#             "role": "user",
#             "content": "What is the current weather in London?"
#         },
#         {
#             "role": "function_call",
#             "content": "{\n    \"name\": \"get_current_weather\",\n    \"arguments\": {\n        \"city\": \"London\"\n    }\n}"
#         },
#         {
#             "role": "function_response",
#             "content": "{\n    \"temperature\": \"15 C\",\n    \"condition\": \"Cloudy\"\n}"
#         },
#         {
#             "role": "assistant",
#             "content": "The current weather in London is Cloudy with a temperature of 15 Celsius"
#         }
#     ],
#     "function_call": {"name": "get_flight_info"},
#     "stream": False,
# }

# api_request_json = {
#   # 'model': 'llama3.1',
#   "model": "hf.co/mradermacher/Llama3-8B-function-calling-uncensored-i1-GGUF:Q4_K_M",
#   'functions': [
#       {
#           "name": "get_flight_info",
#           "description": "Get flight information between two locations",
#           "parameters": {
#               "type": "object",
#               "properties": {
#                   "loc_origin": {
#                       "type": "string",
#                       "description": "The departure airport, e.g. DUS"
#                   },
#                   "loc_destination": {
#                       "type": "string",
#                       "description": "The destination airport, e.g. HAM"
#                   }
#               },
#           "required": ["loc_origin", "loc_destination"]
#           }
#       }
#   ],
#   "stream": False,
#   "function_call": {"name": "get_flight_info"},
#   'messages': [
#       {'role': 'user', 'content': "When's the next flight from Amsterdam to New York?"}],
# }
#
# # Execute the Request
# response = llama.run(api_request_json)
# # output = response.json()['choices'][0]['message']
#
#
#
# output=response.content.decode("utf-8")
# pprint.pp(output, indent=2)
