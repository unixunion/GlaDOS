"""LangChain/Ollama LLM backend.

Uses ChatOllama from langchain_ollama for native Ollama integration.
Tool execution happens inline during streaming (LangChain handles it).
"""

from typing import Iterator

from loguru import logger

from glados.llm.backends.base import LLMBackend, StreamChunk, ToolCallDelta


class LangChainBackend(LLMBackend):
    """Backend for LangChain (Ollama native)."""

    def __init__(self, config):
        self.client = self.create_client(config)

    def create_client(self, config):
        from langchain_ollama import ChatOllama
        from glados.system.plugin import PluginSystem
        from glados.llm.client_type import ClientType
        tools = PluginSystem().get_available_tools(architecture=ClientType.LANGCHAIN)
        return ChatOllama(
            model=config.model,
            temperature=0,
            base_url=config.completion_url,
            seed=42,
        ).bind_tools(tools)

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
        logger.info("Calling langchain client...")

        for chunk in self.client.stream(messages):
            sc = StreamChunk()

            if chunk.tool_calls:
                for i, tc in enumerate(chunk.tool_calls):
                    sc.tool_call_deltas.append(ToolCallDelta(
                        index=i,
                        id=tc.get("id"),
                        name=tc.get("name"),
                        arguments=str(tc.get("args", {})),
                    ))
            elif chunk.content:
                sc.content = chunk.content

            if sc.content or sc.tool_call_deltas:
                yield sc
