---
name: Model performance observations
description: Which LLM models work well/poorly for GlaDOS tool calling and instruction following
type: feedback
---

Mistral Small 24B (`mistral-small-24b-instruct-2501`) works well for tool calling and display usage — better instruction following than Qwen MoE models despite being smaller. Slower but more reliable.

Qwen 3 30B-A3B (MoE, 3B active): works but unreliable tool calling — will hallucinate having called tools without actually calling them, even with `tool_choice='required'`.

Qwen 3.5 35B-A3B: has Jinja template issues in LM Studio ("No user query found in messages"), may need lmstudio-community version or ChatML template override.

**Why:** Small active-parameter MoE models struggle with tool call discipline. Mistral Small 24B is the current best balance of speed and reliability.

**How to apply:** When discussing model choices or debugging tool-calling issues, consider the model's tool-calling reliability. Don't assume tool_choice='required' will be respected by all models.
