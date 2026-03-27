import importlib
import random
import string
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
        self.thinking_enabled: bool = getattr(config, 'thinking_enabled', False)
        self.max_response_tokens: int = getattr(config, 'max_response_tokens', 200)

    def stream_response(self, tools=None, model: str = None, query: str = None, confidence_threshold=0.5, memory_context: str = None) -> EventStream[CompletionEvent] | Stream[ChatCompletionChunk]:
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
                            # Use "required" to force the model to call a tool when intent
                            # classifier is confident. "auto" lets local models hallucinate
                            # tool responses instead of actually calling them.
                            tool_choice = "required"
                            logger.info(f"Intent classifier matched '{predicted_intent}' with confidence {confidence:.2f}, using tool_choice='required'")
                    except Exception as e:
                        logger.exception(f"Intent classifier threw exception, {e}")

            else:
                logger.warning(f"tool_choice not implemented for client_type: {self.client_type}, FIXME")

            logger.debug(f"Making request with messages\n\n{self.message_manager.get_messages()}")
            if self.client_type is ClientType.OPENAI:
                logger.info("Calling openai client")
                messages = list(self.message_manager.get_messages())
                # Inject memory context after last system message
                if memory_context:
                    insert_idx = 0
                    for i, msg in enumerate(messages):
                        if msg.get("role") == "system":
                            insert_idx = i + 1
                    messages.insert(insert_idx, {"role": "system", "content": memory_context})
                # Suppress thinking/reasoning for faster responses when disabled
                if not self.thinking_enabled and messages and messages[0].get("role") == "system":
                    messages[0] = dict(messages[0])
                    messages[0]["content"] += "\n\nIMPORTANT: Do not use thinking tags, internal reasoning, or chain-of-thought. Do not narrate your thought process. Do not say things like 'The user is asking...' or 'I should respond...'. Just respond directly to the user with your answer. No meta-commentary."
                logger.info(f"Messages being sent to LLM ({len(messages)} messages): "
                            f"{[{'role': m.get('role'), 'has_tool_calls': bool(m.get('tool_calls')), 'has_tool_call_id': bool(m.get('tool_call_id'))} for m in messages if isinstance(m, dict)]}")
                # Log each message with content preview for diagnostics
                for i, m in enumerate(messages):
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role", "?")
                    content = m.get("content", "")
                    tc = m.get("tool_calls")
                    if content:
                        preview = str(content)[:150].replace("\n", " ")
                        logger.info(f"  [{i}] {role}: {preview}")
                    elif tc:
                        names = [t.get("function", {}).get("name", "?") for t in tc if isinstance(t, dict)]
                        logger.info(f"  [{i}] {role}: [tool_calls: {', '.join(names)}]")
                    else:
                        logger.info(f"  [{i}] {role}: (empty)")
                # Sanitize tool_call_ids to match [a-zA-Z0-9]{9} (required by Mistral templates)
                for msg in messages:
                    if isinstance(msg, dict):
                        if msg.get("tool_call_id") and not _is_valid_tool_call_id(msg["tool_call_id"]):
                            msg["tool_call_id"] = _generate_tool_call_id()
                        if msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                if isinstance(tc, dict) and not _is_valid_tool_call_id(tc.get("id", "")):
                                    tc["id"] = _generate_tool_call_id()

                # Only cap tokens for text responses (no tools) when thinking is disabled.
                # When thinking is enabled, max_tokens would include reasoning tokens,
                # cutting off the actual response. Rely on wall-clock + repetition guards instead.
                max_tokens = NOT_GIVEN
                if not tools and not self.thinking_enabled and self.max_response_tokens:
                    max_tokens = self.max_response_tokens
                response: Stream[ChatCompletionChunk] = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    tools=tools or NOT_GIVEN,
                    tool_choice=tool_choice if tools else NOT_GIVEN,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    timeout=30.0,
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


def _generate_tool_call_id() -> str:
    """Generate a 9-char alphanumeric ID compatible with Mistral's template."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=9))


def _is_valid_tool_call_id(tool_call_id: str) -> bool:
    """Check if a tool_call_id matches [a-zA-Z0-9]{9}."""
    return bool(tool_call_id) and len(tool_call_id) == 9 and tool_call_id.isalnum()
