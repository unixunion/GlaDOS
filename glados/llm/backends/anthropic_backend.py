"""Anthropic Claude LLM backend.

Supports Claude models via the Anthropic API. Handles message format
conversion (OpenAI → Anthropic), tool use, and streaming.

Requires: pip install anthropic
"""

from typing import Iterator

from loguru import logger

from glados.llm.backends.base import LLMBackend, StreamChunk, ToolCallDelta


class AnthropicBackend(LLMBackend):
    """Backend for Anthropic Claude API."""

    def __init__(self, config):
        self.client = self.create_client(config)
        self.max_response_tokens = getattr(config, 'max_response_tokens', 4096)

    def create_client(self, config):
        try:
            import anthropic
            import os
            # Prefer config, fall back to ANTHROPIC_API_KEY env var, then let SDK auto-detect
            api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")
            return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert OpenAI-format messages to Anthropic format.

        Returns (system_prompt, messages) where system_prompt is extracted
        from system messages and messages are user/assistant only.
        """
        system_parts = []
        anthropic_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(str(content))
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": str(content) if content else "..."})
            elif role == "assistant":
                if msg.get("tool_calls"):
                    # Convert OpenAI tool_calls to Anthropic tool_use blocks
                    tool_blocks = []
                    for tc in msg["tool_calls"]:
                        if isinstance(tc, dict):
                            import json
                            tool_blocks.append({
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "input": json.loads(tc.get("function", {}).get("arguments", "{}")),
                            })
                    anthropic_messages.append({"role": "assistant", "content": tool_blocks})
                else:
                    anthropic_messages.append({"role": "assistant", "content": str(content) if content else "..."})
            elif role == "tool":
                # Convert OpenAI tool result to Anthropic tool_result
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": str(content) if content else "",
                    }],
                })

        # Merge consecutive same-role messages (Anthropic requires alternating)
        merged = []
        for msg in anthropic_messages:
            if merged and merged[-1]["role"] == msg["role"]:
                # Merge content
                prev_content = merged[-1]["content"]
                new_content = msg["content"]
                if isinstance(prev_content, str) and isinstance(new_content, str):
                    merged[-1]["content"] = prev_content + "\n" + new_content
                elif isinstance(prev_content, list) and isinstance(new_content, list):
                    merged[-1]["content"].extend(new_content)
                elif isinstance(prev_content, str) and isinstance(new_content, list):
                    merged[-1]["content"] = [{"type": "text", "text": prev_content}] + new_content
                elif isinstance(prev_content, list) and isinstance(new_content, str):
                    merged[-1]["content"].append({"type": "text", "text": new_content})
            else:
                merged.append(msg)

        # Ensure first message is from user (Anthropic requirement)
        if merged and merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "content": "Hello."})

        return "\n\n".join(system_parts), merged

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert OpenAI-format tools to Anthropic format.

        Cleans up JSON Schema to match Claude's requirements (draft 2020-12):
        - Remove null enum values
        - Remove additionalProperties (Claude doesn't support it in tool schemas)
        - Ensure all properties have a type
        """
        if not tools:
            return None
        anthropic_tools = []
        for tool in tools:
            if isinstance(tool, dict) and tool.get("type") == "function":
                func = tool.get("function", {})
                schema = func.get("parameters", {"type": "object", "properties": {}})
                # Deep clean the schema
                schema = self._clean_schema(schema)
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": schema,
                })
        return anthropic_tools or None

    def _clean_schema(self, schema: dict) -> dict:
        """Clean a JSON Schema dict for Claude compatibility."""
        if not isinstance(schema, dict):
            return schema
        cleaned = {}
        for key, value in schema.items():
            # Remove additionalProperties (Claude doesn't support in tool schemas)
            if key == "additionalProperties":
                continue
            # Clean null values from enum
            if key == "enum" and value is None:
                continue
            # Recurse into nested dicts
            if isinstance(value, dict):
                cleaned[key] = self._clean_schema(value)
            elif key == "properties" and isinstance(value, dict):
                # Clean each property definition
                cleaned[key] = {k: self._clean_schema(v) for k, v in value.items()}
            else:
                cleaned[key] = value
        return cleaned

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
        # Inject memory context
        memory_context = kwargs.get("memory_context")
        if memory_context:
            messages = list(messages)
            insert_idx = 0
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    insert_idx = i + 1
            messages.insert(insert_idx, {"role": "system", "content": memory_context})

        system_prompt, anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        logger.info(f"Calling Anthropic Claude API (model: {model})")
        logger.info(f"  System prompt: {system_prompt[:150]}...")
        logger.info(f"  Messages: {len(anthropic_messages)}")

        # Map tool_choice
        tc = {"type": "auto"}
        if tool_choice == "required":
            tc = {"type": "any"}
        elif tool_choice == "none" or not anthropic_tools:
            tc = None

        create_kwargs = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or self.max_response_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            create_kwargs["system"] = system_prompt
        if anthropic_tools:
            create_kwargs["tools"] = anthropic_tools
            if tc:
                create_kwargs["tool_choice"] = tc

        with self.client.messages.stream(**create_kwargs) as stream:
            current_tool_index = -1
            for event in stream:
                sc = StreamChunk()

                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        current_tool_index += 1
                        sc.tool_call_deltas.append(ToolCallDelta(
                            index=current_tool_index,
                            id=event.content_block.id,
                            name=event.content_block.name,
                            arguments="",
                        ))
                        yield sc
                        continue

                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        sc.content = event.delta.text
                    elif event.delta.type == "input_json_delta":
                        sc.tool_call_deltas.append(ToolCallDelta(
                            index=current_tool_index,
                            arguments=event.delta.partial_json,
                        ))

                elif event.type == "message_stop":
                    sc.finish_reason = "stop"

                if sc.content or sc.tool_call_deltas or sc.finish_reason:
                    yield sc
