"""LLM Backend abstraction — normalized interface for all LLM providers.

Each backend implements stream() which yields StreamChunk objects.
The caller (StreamHandler/ChatClient) works with normalized chunks
regardless of whether the backend is OpenAI, Anthropic, or LangChain.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class ToolCallDelta:
    """A tool call fragment from a streaming response."""
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""


@dataclass
class StreamChunk:
    """Normalized chunk from any LLM backend.

    Each chunk contains either content (text token) or tool call deltas,
    never both. The caller accumulates tool call deltas across chunks
    and executes them after the stream completes.
    """
    content: Optional[str] = None
    tool_call_deltas: list[ToolCallDelta] = field(default_factory=list)
    finish_reason: Optional[str] = None


class LLMBackend(ABC):
    """Abstract base class for LLM backends.

    Subclass this to add a new LLM provider (OpenAI, Anthropic, etc.).
    """

    @abstractmethod
    def create_client(self, config) -> Any:
        """Create the LLM client from config. Called once during init."""
        pass

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str = "",
        tool_choice: str = "auto",
        max_tokens: int | None = None,
        temperature: float = 0.0,
        **kwargs,
    ) -> Iterator[StreamChunk]:
        """Stream a response from the LLM.

        Args:
            messages: Chat messages in OpenAI format (role/content dicts)
            tools: Tool definitions in OpenAI format (optional)
            model: Model name/ID
            tool_choice: "auto", "required", or "none"
            max_tokens: Max response tokens (None for unlimited)
            temperature: Sampling temperature

        Yields:
            StreamChunk objects with either content or tool_call_deltas
        """
        pass

    @staticmethod
    def from_config(config) -> "LLMBackend":
        """Factory: create the right backend from config.client_type."""
        client_type = config.client_type.upper()
        if client_type == "OPENAI":
            from glados.llm.backends.openai_backend import OpenAIBackend
            return OpenAIBackend(config)
        elif client_type == "ANTHROPIC":
            from glados.llm.backends.anthropic_backend import AnthropicBackend
            return AnthropicBackend(config)
        elif client_type == "LANGCHAIN":
            from glados.llm.backends.langchain_backend import LangChainBackend
            return LangChainBackend(config)
        else:
            raise ValueError(f"Unsupported client_type: {config.client_type}")
