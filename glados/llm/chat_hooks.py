"""Chat pipeline hooks — allows plugins to register synchronous callbacks at defined phases."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any

from loguru import logger

from glados.context.activity import Activity


class ChatPipelinePhase(Enum):
    PRE_LLM = "pre_llm"             # After classification, before LLM call
    POST_TOOL = "post_tool"          # After tool execution, before LLM summarization
    POST_RESPONSE = "post_response"  # After LLM response stored


@dataclass
class ChatContext:
    """Mutable context passed through pipeline hooks.

    Hooks can modify fields to influence downstream behavior:
    - Set memory_context to inject context into the LLM call
    - Set handled=True to stop the pipeline (hook already spoke via TTS)
    - Use extra dict to pass data between hooks
    """
    user_text: str
    activity: Activity
    session_id: str
    tts_queue: Any  # queue.Queue
    message_manager: Any = None  # MessageManager instance for conversation history
    memory_context: str | None = None
    handled: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class ChatHook:
    name: str
    phase: ChatPipelinePhase
    callback: Callable[[ChatContext], None]
    priority: int = 0  # lower = runs first


class ChatHookRegistry:
    """Singleton registry for chat pipeline hooks."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._hooks: list[ChatHook] = []
        return cls._instance

    def register(self, hook: ChatHook):
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)
        logger.info(f"[ChatHooks] Registered hook '{hook.name}' for phase {hook.phase.value} (priority={hook.priority})")

    def get_hooks(self, phase: ChatPipelinePhase) -> list[ChatHook]:
        return [h for h in self._hooks if h.phase == phase]

    def run_hooks(self, phase: ChatPipelinePhase, ctx: ChatContext) -> None:
        """Run all hooks for a phase in priority order. Stops if ctx.handled is set."""
        for hook in self.get_hooks(phase):
            try:
                hook.callback(ctx)
                if ctx.handled:
                    logger.debug(f"[ChatHooks] Pipeline halted by hook '{hook.name}' at {phase.value}")
                    return
            except Exception as e:
                logger.error(f"[ChatHooks] Error in hook '{hook.name}': {e}")
