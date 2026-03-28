"""OpenAI-compatible LLM backend.

Works with OpenAI API, LM Studio, Ollama (via OpenAI-compatible endpoint),
and any server that implements the OpenAI Chat Completions API.
"""

import random
import string
from typing import Iterator

from loguru import logger
from openai import OpenAI, NOT_GIVEN

from glados.llm.backends.base import LLMBackend, StreamChunk, ToolCallDelta


def _generate_tool_call_id() -> str:
    """Generate a 9-char alphanumeric ID compatible with Mistral's template."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=9))


def _sanitize_tool_call_ids(messages: list[dict]):
    """Ensure all tool_call_ids are 9-char alphanumeric (Mistral template compat)."""
    for msg in messages:
        if isinstance(msg, dict):
            if msg.get("tool_call_id") and not (len(msg["tool_call_id"]) == 9 and msg["tool_call_id"].isalnum()):
                msg["tool_call_id"] = _generate_tool_call_id()
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and not (len(tc.get("id", "")) == 9 and tc.get("id", "").isalnum()):
                        tc["id"] = _generate_tool_call_id()


class OpenAIBackend(LLMBackend):
    """Backend for OpenAI-compatible APIs."""

    def __init__(self, config):
        self.client = self.create_client(config)
        self.thinking_enabled = getattr(config, 'thinking_enabled', False)
        self.max_response_tokens = getattr(config, 'max_response_tokens', 500)

    def create_client(self, config):
        return OpenAI(
            base_url=config.completion_url,
            api_key=config.api_key or "not-needed",
            timeout=20.0,
        )

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
        messages = list(messages)  # Don't mutate the original

        # Inject memory context after last system message
        memory_context = kwargs.get("memory_context")
        if memory_context:
            insert_idx = 0
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    insert_idx = i + 1
            messages.insert(insert_idx, {"role": "system", "content": memory_context})

        # Suppress thinking/reasoning when disabled
        if not self.thinking_enabled and messages and messages[0].get("role") == "system":
            messages[0] = dict(messages[0])
            messages[0]["content"] += (
                "\n\nIMPORTANT: Do not use thinking tags, internal reasoning, or chain-of-thought. "
                "Do not narrate your thought process. Just respond directly to the user."
            )

        # Log messages for diagnostics
        logger.info(f"Calling openai client")
        logger.info(f"Messages being sent to LLM ({len(messages)} messages): "
                     f"{[{'role': m.get('role'), 'has_tool_calls': bool(m.get('tool_calls')), 'has_tool_call_id': bool(m.get('tool_call_id'))} for m in messages if isinstance(m, dict)]}")
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            role = m.get("role", "?")
            content = m.get("content", "")
            tc = m.get("tool_calls")
            if content:
                logger.info(f"  [{i}] {role}: {str(content)[:150].replace(chr(10), ' ')}")
            elif tc:
                names = [t.get("function", {}).get("name", "?") for t in tc if isinstance(t, dict)]
                logger.info(f"  [{i}] {role}: [tool_calls: {', '.join(names)}]")
            else:
                logger.info(f"  [{i}] {role}: (empty)")

        # Sanitize tool_call_ids
        _sanitize_tool_call_ids(messages)

        # Token limit: only cap for text responses (no tools) when thinking is disabled
        effective_max_tokens = NOT_GIVEN
        if max_tokens and not tools and not self.thinking_enabled:
            effective_max_tokens = max_tokens

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            tools=tools or NOT_GIVEN,
            tool_choice=tool_choice if tools else NOT_GIVEN,
            max_tokens=effective_max_tokens,
            temperature=temperature,
            timeout=30.0,
        )

        # Yield normalized StreamChunks
        for chunk in response:
            sc = StreamChunk()
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            if choice.delta.tool_calls:
                for tc_delta in choice.delta.tool_calls:
                    sc.tool_call_deltas.append(ToolCallDelta(
                        index=tc_delta.index,
                        id=tc_delta.id,
                        name=tc_delta.function.name if tc_delta.function else None,
                        arguments=tc_delta.function.arguments if tc_delta.function else "",
                    ))
            elif choice.delta.content:
                sc.content = choice.delta.content

            if choice.finish_reason:
                sc.finish_reason = choice.finish_reason

            yield sc
