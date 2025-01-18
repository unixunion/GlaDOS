import threading
import time


class TTSPlaybackManager:
    def __init__(self):
        self.playing_start_time = None
        self.playing_duration = 0
        self.lock = threading.Lock()

    def start_playback(self, duration: float):
        """Mark the start of TTS playback."""
        with self.lock:
            self.playing_start_time = time.time()
            self.playing_duration = duration

    def is_playback_active(self) -> bool:
        """Check if TTS playback is active."""
        with self.lock:
            if self.playing_start_time is None:
                return False
            elapsed = time.time() - self.playing_start_time
            return elapsed < self.playing_duration

    def stop_playback(self):
        """Stop playback tracking."""
        with self.lock:
            self.playing_start_time = None
            self.playing_duration = 0
