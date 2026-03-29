"""Shared extraction utilities for NLP mode parameter parsing."""

import re
from typing import Optional

# Word-to-number map for 0-99
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def word_to_number(text: str) -> Optional[int]:
    """Convert a number word or digit string to int. Returns None if not recognized.

    Handles: "twelve" -> 12, "42" -> 42, "twenty five" -> 25, "twenty-five" -> 25
    """
    text = text.strip().lower().replace("-", " ")

    # Try direct digit parse
    try:
        return int(text)
    except ValueError:
        pass

    # Single word
    if text in _ONES:
        return _ONES[text]
    if text in _TENS:
        return _TENS[text]

    # Two-word compound: "twenty five"
    parts = text.split()
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES:
        return _TENS[parts[0]] + _ONES[parts[1]]

    # "a" as 1: "a minute" -> 1
    if text == "a" or text == "an":
        return 1

    return None


# Individual unit patterns for duration extraction
_HOURS_PATTERN = re.compile(r"(?P<hours>\w+)\s+hours?", re.IGNORECASE)
_MINUTES_PATTERN = re.compile(r"(?P<minutes>\w+)\s+minutes?", re.IGNORECASE)
_SECONDS_PATTERN = re.compile(r"(?P<seconds>\w+)\s+seconds?", re.IGNORECASE)


def parse_duration(text: str) -> Optional[dict]:
    """Extract hours, minutes, seconds from a duration expression.

    Returns dict with keys hours, minutes, seconds (ints), or None if nothing found.
    Examples:
        "12 minutes" -> {"hours": 0, "minutes": 12, "seconds": 0}
        "one hour and thirty seconds" -> {"hours": 1, "minutes": 0, "seconds": 30}
        "a minute" -> {"hours": 0, "minutes": 1, "seconds": 0}
    """
    text = text.strip().lower()

    result = {"hours": 0, "minutes": 0, "seconds": 0}
    found_any = False

    for pattern, unit in [
        (_HOURS_PATTERN, "hours"),
        (_MINUTES_PATTERN, "minutes"),
        (_SECONDS_PATTERN, "seconds"),
    ]:
        m = pattern.search(text)
        if m:
            raw = m.group(unit)
            val = word_to_number(raw)
            if val is not None:
                result[unit] = val
                found_any = True

    if found_any:
        return result
    return None


def extract_after_keyword(text: str, keywords: list[str]) -> Optional[str]:
    """Extract text appearing after any of the given keywords.

    Example:
        extract_after_keyword("set a timer for eggs", ["for", "called"]) -> "eggs"
        extract_after_keyword("play bohemian rhapsody", ["play"]) -> "bohemian rhapsody"
    """
    for kw in keywords:
        pattern = re.compile(r"\b" + re.escape(kw) + r"\s+(.+)", re.IGNORECASE)
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return None


def extract_location(text: str) -> Optional[str]:
    """Extract a location from weather-related queries.

    Handles: "weather in London", "what's it like in Paris", "temperature for NYC"
    Falls back to None if no location pattern found.
    """
    patterns = [
        re.compile(r"\b(?:in|for|at)\s+(.+?)(?:\s*\??\s*)$", re.IGNORECASE),
    ]
    for p in patterns:
        m = p.search(text)
        if m:
            loc = m.group(1).strip().rstrip("?.,!")
            if loc:
                return loc
    return None


def extract_music_action(text: str) -> tuple[str, Optional[str]]:
    """Extract music action and optional query from text.

    Returns (action, query) where action is one of PLAY/PAUSE/RESUME/STOP/SKIP/PREVIOUS.
    """
    text_lower = text.lower().strip()

    # Check for control commands first
    if any(w in text_lower for w in ("pause",)):
        return "PAUSE", None
    if any(w in text_lower for w in ("resume", "unpause", "continue playing")):
        return "RESUME", None
    if re.search(r"\b(stop|turn off)\b.*\b(music|song|playing|track)\b", text_lower):
        return "STOP", None
    if any(w in text_lower for w in ("next track", "skip song", "skip track", "next song", "skip")):
        return "SKIP", None
    if any(w in text_lower for w in ("previous track", "previous song", "go back")):
        return "PREVIOUS", None

    # Play with query
    play_match = re.search(
        r"\b(?:play|put on|listen to)\s+(.+)", text_lower
    )
    if play_match:
        query = play_match.group(1).strip()
        # Clean up common filler
        query = re.sub(r"^(?:some|the song|the album|the playlist|the artist|the band)\s+", "", query)
        return "PLAY", query if query else None

    # Default: if it got classified as music, assume play
    return "PLAY", text_lower
