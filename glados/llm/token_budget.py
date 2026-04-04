"""Token budget management for LLM context window.

Provides rough token estimation and per-layer budget tracking.
Uses character-based estimation (~4 chars per token) which is
accurate enough for preventing context window overflow.
"""
from loguru import logger


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    if not text:
        return 0
    return len(str(text)) // 4 + 1


class TokenBudget:
    """Tracks token usage across context layers and enforces budgets."""

    def __init__(self, max_tokens: int = 4096, response_reserve: int = 500):
        self.max_tokens = max_tokens
        self.response_reserve = response_reserve
        self.available = max_tokens - response_reserve
        self._layers: dict[str, int] = {}  # layer_name → estimated tokens

    def measure(self, layer: str, text: str) -> int:
        """Measure and record token usage for a layer (no truncation)."""
        tokens = estimate_tokens(text)
        self._layers[layer] = tokens
        return tokens

    def allocate(self, layer: str, text: str, max_pct: float = 1.0) -> str:
        """Allocate tokens for a layer, truncating if over budget.

        Args:
            layer: Name of the context layer (e.g., "memory", "knowledge")
            text: The text to allocate
            max_pct: Maximum percentage of available budget for this layer (0.0-1.0)

        Returns:
            The text, possibly truncated to fit the budget.
        """
        if not text:
            self._layers[layer] = 0
            return text

        tokens = estimate_tokens(text)
        budget = int(self.available * max_pct)

        if tokens <= budget:
            self._layers[layer] = tokens
            return text

        # Truncate to fit budget
        char_limit = max(budget * 4, 100)  # At least 100 chars
        truncated = text[:char_limit].rsplit("\n", 1)[0] + "\n[...truncated due to context budget]"
        self._layers[layer] = estimate_tokens(truncated)
        logger.info(f"[TokenBudget] Truncated '{layer}': {tokens} → {self._layers[layer]} tokens (budget: {budget})")
        return truncated

    def used(self) -> int:
        """Total tokens used across all layers."""
        return sum(self._layers.values())

    def remaining(self) -> int:
        """Tokens remaining in budget."""
        return max(0, self.available - self.used())

    def usage_pct(self) -> float:
        """Percentage of available budget used."""
        return (self.used() / self.available * 100) if self.available > 0 else 0

    def summary(self) -> dict:
        """Return a summary dict for logging and UI display."""
        return {
            "max_tokens": self.max_tokens,
            "response_reserve": self.response_reserve,
            "available": self.available,
            "used": self.used(),
            "remaining": self.remaining(),
            "usage_pct": round(self.usage_pct(), 1),
            "layers": dict(self._layers),
        }

    def log_usage(self):
        """Log token usage breakdown."""
        total = self.used()
        pct = self.usage_pct()
        parts = " | ".join(f"{k}:{v}" for k, v in self._layers.items() if v > 0)
        logger.info(f"[Context] {total}/{self.available} tokens ({pct:.0f}%) — {parts}")
