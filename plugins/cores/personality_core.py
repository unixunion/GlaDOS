"""Personality Core — injects contextual GLaDOS quips after responses.

Loads themed quote files from data/glados_quotes/ and occasionally appends
a relevant quip after the LLM response finishes speaking. Uses keyword matching
to pick a theme that fits what the user said, with probability + cooldown
to keep it from being annoying.
"""
import os
import random
import time

from loguru import logger

from glados.context.activity import Activity
from glados.llm.chat_hooks import ChatPipelinePhase, ChatContext
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage

# Theme → trigger keywords + valid activities
THEME_TRIGGERS = {
    "passive_aggressive": {
        "keywords": ["good job", "well done", "thanks", "thank you", "nice", "great", "perfect", "awesome"],
        "activities": {Activity.GENERAL, Activity.COOKING, Activity.UTILITIES},
    },
    "dark_humor": {
        "keywords": ["dangerous", "warning", "careful", "fire", "broken", "error", "fail", "dead", "kill", "destroy"],
        "activities": {Activity.GENERAL, Activity.UTILITIES, Activity.SYSTEM},
    },
    "science": {
        "keywords": ["how", "why", "explain", "calculate", "convert", "temperature", "degrees", "measure"],
        "activities": {Activity.GENERAL, Activity.UTILITIES},
    },
    "insults": {
        "keywords": [],  # never keyword-triggered, only random fallback
        "activities": {Activity.GENERAL},
    },
    "fake_empathy": {
        "keywords": ["sorry", "help", "can't", "don't know", "confused", "wrong", "mistake", "oops"],
        "activities": {Activity.GENERAL},
    },
    "food_cake": {
        "keywords": ["recipe", "cook", "bake", "food", "eat", "hungry", "cake", "ingredients", "dinner", "lunch"],
        "activities": {Activity.COOKING, Activity.GENERAL},
    },
    "time_waiting": {
        "keywords": ["wait", "slow", "when", "how long", "timer", "late", "hurry", "patience"],
        "activities": {Activity.GENERAL, Activity.UTILITIES, Activity.COOKING},
    },
}

QUOTES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "glados_quotes")


class PersonalityCore(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        self.enabled = self.plugin_config.get("enabled", True)
        self.quip_chance = self.plugin_config.get("quip_chance", 0.15)
        self.cooldown = self.plugin_config.get("cooldown_seconds", 120)

        # Load quote files
        self.quotes: dict[str, list[str]] = {}
        self.used: dict[str, set[int]] = {}
        self.last_quip_time = 0.0
        self._pending_quip: str | None = None

        enabled_themes = self.plugin_config.get("themes", list(THEME_TRIGGERS.keys()))
        self._load_quotes(enabled_themes)

    def _load_quotes(self, themes: list[str]):
        """Load quote files from data/glados_quotes/."""
        for theme in themes:
            path = os.path.join(QUOTES_DIR, f"{theme}.txt")
            if not os.path.exists(path):
                logger.warning(f"[PersonalityCore] Quote file not found: {path}")
                continue
            with open(path) as f:
                lines = [line.strip() for line in f if line.strip()]
            if lines:
                self.quotes[theme] = lines
                self.used[theme] = set()
                logger.info(f"[PersonalityCore] Loaded {len(lines)} quotes for theme '{theme}'")
        logger.success(f"[PersonalityCore] {len(self.quotes)} themes loaded, {sum(len(q) for q in self.quotes.values())} total quotes")

    def start(self):
        if not self.enabled:
            logger.info("[PersonalityCore] Disabled via config")
            return
        if not self.quotes:
            logger.warning("[PersonalityCore] No quotes loaded, disabling")
            return

        self.register_chat_hook(
            phase=ChatPipelinePhase.POST_RESPONSE,
            callback=self._post_response_hook,
            priority=50,
        )
        logger.info(f"[PersonalityCore] Active — {self.quip_chance:.0%} chance, {self.cooldown}s cooldown")

    def stop(self):
        pass

    # ---------------------------------------------------------------------------
    # Hook
    # ---------------------------------------------------------------------------

    def _post_response_hook(self, ctx: ChatContext):
        """Decide whether to append a quip after this response."""
        if not self._should_fire():
            return

        theme = self._match_theme(ctx.user_text, ctx.activity)
        if not theme:
            return

        quip = self._pick_quote(theme)
        if quip:
            logger.info(f"[PersonalityCore] Injecting '{theme}' quip: {quip[:60]}...")
            ctx.tts_queue.put(quip)
            self.last_quip_time = time.time()

    # ---------------------------------------------------------------------------
    # Logic
    # ---------------------------------------------------------------------------

    def _should_fire(self) -> bool:
        """Probability + cooldown check."""
        if random.random() >= self.quip_chance:
            logger.info(f"[PersonalityCore] chance failed")
            return False
        if time.time() - self.last_quip_time < self.cooldown:
            logger.info(f"[PersonalityCore] in cooldown")
            return False
        logger.info(f"[PersonalityCore] success!")
        return True

    def _match_theme(self, user_text: str, activity: Activity) -> str | None:
        """Match user text + activity to a quote theme."""
        text_lower = user_text.lower()
        matched = []

        for theme, config in THEME_TRIGGERS.items():
            if theme not in self.quotes:
                continue
            # Check activity
            if config["activities"] and activity not in config["activities"]:
                continue
            # Check keywords
            if config["keywords"] and any(kw in text_lower for kw in config["keywords"]):
                matched.append(theme)

        if matched:
            return random.choice(matched)

        # No keyword match — 50% chance pick a random activity-valid theme
        if random.random() < 0.5:
            valid = [
                theme for theme, config in THEME_TRIGGERS.items()
                if theme in self.quotes
                and (not config["activities"] or activity in config["activities"])
                and config["keywords"]  # skip keyword-less themes (insults) for random
            ]
            if valid:
                return random.choice(valid)

        return None

    def _pick_quote(self, theme: str) -> str | None:
        """Pick a random quote from a theme, avoiding recent repeats."""
        quotes = self.quotes.get(theme)
        if not quotes:
            return None

        used = self.used.get(theme, set())
        available = [i for i in range(len(quotes)) if i not in used]

        if not available:
            # All used — reset
            self.used[theme] = set()
            available = list(range(len(quotes)))

        idx = random.choice(available)
        self.used[theme].add(idx)
        return quotes[idx]
