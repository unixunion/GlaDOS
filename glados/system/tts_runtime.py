"""Shared TTS runtime registry for coordinating multiple voice cores.

main.py registers the primary ``speaking_lock`` here at startup; plugins
that spin up their own SpeechModule instances (e.g. the ebook reader's
Kokoro narrator) read the same lock so the mic stays silent while any
voice core is talking, and wake-word interrupts apply to all of them.

Analogous to :class:`glados.system.event_system.EventSystem` — a tiny
framework-level singleton, not plugin-specific.
"""

import threading
from typing import Optional


class TTSRuntime:
    _instance = None
    _speaking_lock: Optional[threading.Event] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def set_speaking_lock(self, lock: threading.Event) -> None:
        self._speaking_lock = lock

    def get_speaking_lock(self) -> threading.Event:
        # Degraded fallback for headless/test contexts where main.py has
        # not registered a lock — returns a fresh event so callers don't
        # crash, but mic-silencing will not be coordinated with the main
        # assistant in that case.
        if self._speaking_lock is None:
            self._speaking_lock = threading.Event()
        return self._speaking_lock
