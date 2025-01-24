from typing import List

from loguru import logger
from openai import OpenAI, NOT_GIVEN, Stream
from openai.types.chat import ChatCompletionChunk

from glados.config import GladosConfig
from glados.llm.message_manager import MessageManager
from plugins.intent_classifier import IntentClassifier
from plugins.plugin_system.plugin_manager import PluginManager



class StreamHandler:
    def __init__(self, client, model, message_manager: MessageManager, config: GladosConfig):
        self.client: OpenAI = client
        self.model: str = model
        self.message_manager = message_manager
        self.plugin_manager: PluginManager = PluginManager()
        self.confidence_threshold = config.plugin_intent_threshold

    def stream_response(self, tools=None, model: str = None, query: str = None, confidence_threshold=0.5):
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
            tool_choice = "auto"

            # Determine the most likely tool choice using the intent classifier if a filter is provided
            if query and self.plugin_manager.get_intent_classifier():
                try:
                    predicted_intent, confidence = self.plugin_manager.get_intent_classifier().predict_intent(query)
                    if confidence >= confidence_threshold:
                        tool_choice = predicted_intent
                        logger.info(f"Tool choice '{tool_choice}' selected with confidence {confidence:.2f}")
                except Exception as e:
                    logger.exception(f"Intent classifier threw exception, {e}")

            logger.debug(f"Making request with messages\n\n{self.message_manager.get_messages()}")
            response: Stream[ChatCompletionChunk] = self.client.chat.completions.create(
                model=model,
                messages=self.message_manager.get_messages(),
                stream=True,
                tools=tools or NOT_GIVEN,
                tool_choice=tool_choice,
                temperature=0.0,
            )
            return response
        except Exception as e:
            logger.exception(f"Error in streaming response: {e}")
            raise

