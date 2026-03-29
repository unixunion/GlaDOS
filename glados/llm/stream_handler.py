"""Stream handler — sends requests to the LLM backend and returns the response stream.

Handles intent classification for tool_choice optimization and delegates
actual streaming to the LLMBackend implementation.
"""

from typing import Iterator

from loguru import logger

from glados.config import GladosConfig
from glados.llm.backends import LLMBackend, StreamChunk
from glados.llm.message_manager import MessageManager
from glados.system.plugin import PluginSystem


class StreamHandler:
    def __init__(self, backend: LLMBackend, model: str, message_manager: MessageManager, config: GladosConfig):
        self.model = model
        self.backend = backend
        self.message_manager = message_manager
        self.plugin_manager = PluginSystem()
        self.confidence_threshold = config.plugin_intent_threshold
        self.max_response_tokens = getattr(config, 'max_response_tokens', 500)

    def stream_response(
        self,
        tools=None,
        model: str = None,
        query: str = None,
        confidence_threshold: float = 0.5,
        memory_context: str = None,
    ) -> Iterator[StreamChunk]:
        """Stream a response from the LLM via the backend.

        Uses the intent classifier to set tool_choice='required' when
        confident about which tool to use.

        Returns:
            Iterator of StreamChunk objects (normalized across all backends).
        """
        tool_choice = "auto"

        # Determine the most likely tool choice using the intent classifier
        if query and self.plugin_manager.get_intent_classifier():
            try:
                predicted_intent, confidence = self.plugin_manager.get_intent_classifier().predict_intent(query)
                # Per-tool threshold overrides global
                effective_threshold = self.plugin_manager.get_nlp_threshold(predicted_intent, default=confidence_threshold)
                if confidence >= effective_threshold:
                    tool_choice = "required"
                    logger.info(f"Intent classifier matched '{predicted_intent}' with confidence {confidence:.2f}, "
                                f"using tool_choice='required' (threshold={effective_threshold:.2f})")
            except Exception as e:
                logger.exception(f"Intent classifier threw exception: {e}")

        messages = list(self.message_manager.get_messages())

        return self.backend.stream(
            messages=messages,
            tools=tools,
            model=model or self.model,
            tool_choice=tool_choice if tools else "none",
            max_tokens=self.max_response_tokens,
            memory_context=memory_context,
        )
