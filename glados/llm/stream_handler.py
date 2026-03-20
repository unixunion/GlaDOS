import importlib
from typing import Optional, Union, Iterator

from langchain_core.messages import BaseMessage, BaseMessageChunk
from langchain_ollama import ChatOllama
from loguru import logger
from openai import OpenAI, NOT_GIVEN, Stream
from openai.types.chat import ChatCompletionChunk

from glados.config import GladosConfig
from glados.llm.client_type import ClientType
from glados.llm.message_manager import MessageManager
from glados.system.plugin import PluginSystem

from mistralai.client import Mistral
from mistralai.client.models import CompletionEvent
from mistralai.client.utils.eventstreaming import EventStream

# Mistral = None
# EventStream = None
# CompletionEvent = None
# try:
#     Mistral = importlib.import_module("mistral").Mistral
#     EventStream = importlib.import_module("mistral").EventStream
#     CompletionEvent = importlib.import_module("mistral").CompletionEvent
# except ImportError:
#     print("Mistral module not available.")
#
# ChatOllama = None
# try:
#     ChatOllama = importlib.import_module("langchain_ollama").ChatOllama
# except ImportError:
#     print("Mistral module not available.")


class StreamHandler:
    def __init__(self, client, model, message_manager: MessageManager, config: GladosConfig):
        self.model: str = model
        self.message_manager = message_manager
        self.plugin_manager: PluginSystem = PluginSystem()
        self.confidence_threshold = config.plugin_intent_threshold
        self.client_type: ClientType = ClientType[config.client_type]
        self.client: Optional[Union[OpenAI, ChatOllama, Mistral]] = client

    def stream_response(self, tools=None, model: str = None, query: str = None, confidence_threshold=0.5) -> EventStream[CompletionEvent] | Stream[ChatCompletionChunk]:
        """
        Streams a response from the model with an optional tool choice determined by the intent classifier.

        Args:
            tools (list): A list of available tools to provide to the model (optional).
            model (str): The model to use for the completion.
            query (str): A query string to determine the most likely tool choice (optional).
            confidence_threshold (float): The minimum confidence required for a tool to be automatically selected.

        Returns:
            Stream[ChatCompletionChunk]: The response stream from the model.
        """
        try:
            tool_choice = 'auto'

            # Determine the most likely tool choice using the intent classifier if a filter is provided
            # the tool_choice can only be any | auto | none | {"type": "function", "function": {"name": "my_function"}}
            if self.client_type is ClientType.OPENAI or self.client_type is ClientType.MISTRAL:
                if query and self.plugin_manager.get_intent_classifier():
                    try:
                        predicted_intent, confidence = self.plugin_manager.get_intent_classifier().predict_intent(query)
                        if confidence >= confidence_threshold:
                            # Use "auto" instead of object-style tool_choice for compatibility
                            # with LM Studio and other OpenAI-compatible endpoints that don't
                            # support {"type": "function", "function": {"name": ...}} format
                            tool_choice = "auto"
                            logger.info(f"Intent classifier matched '{predicted_intent}' with confidence {confidence:.2f}, using tool_choice='auto'")
                    except Exception as e:
                        logger.exception(f"Intent classifier threw exception, {e}")

            else:
                logger.warning(f"tool_choice not implemented for client_type: {self.client_type}, FIXME")

            logger.debug(f"Making request with messages\n\n{self.message_manager.get_messages()}")
            if self.client_type is ClientType.OPENAI:
                logger.info("Calling openai client")
                response: Stream[ChatCompletionChunk] = self.client.chat.completions.create(
                    model=model,
                    messages=self.message_manager.get_messages(),
                    stream=True,
                    tools=tools or NOT_GIVEN,
                    tool_choice=tool_choice,
                    temperature=0.0,
                )
            elif self.client_type is ClientType.MISTRAL:
                logger.error("Calling mistral client, this is not implemented!")
                response: EventStream[CompletionEvent] = self.client.chat.stream(
                    model=model,
                    messages=self.message_manager.get_messages(),
                    tools=tools or None,
                    tool_choice=tool_choice,
                    temperature=0.0,
                )
            elif self.client_type is ClientType.LANGCHAIN:
                logger.info("Calling langchain client...")
                response: Iterator[BaseMessageChunk] = self.client.stream(self.message_manager.get_messages())
                logger.info(f"Response: {response}")
            else:
                raise ValueError(f"Unsupported client type: {self.client_type}")
            return response
        except Exception as e:
            logger.exception(f"Error in streaming response: {e}")
            raise
