"""Test music query classifier — validates pattern-based classification of
music queries into artist, album, track, and playlist/genre types.

No LLM or network needed — tests the regex/keyword classifier.
Run: pytest tests/test_music_classifier.py -v
"""
import pytest
from plugins.music.music_player import MusicPlayer


@pytest.fixture(scope="module")
def classifier():
    """Create a MusicPlayer instance for classification (no Spotify needed)."""
    return MusicPlayer()


# ---------------------------------------------------------------------------
# "X by Y" pattern → track with artist filter
# ---------------------------------------------------------------------------

BY_PATTERN_CASES = [
    ("1996 by the wombats", "track", "1996", "the wombats"),
    ("creep by radiohead", "track", "creep", "radiohead"),
    ("teardrop by massive attack", "track", "teardrop", "massive attack"),
    ("closer by nine inch nails", "track", "closer", "nine inch nails"),
    ("dummy by portishead", "track", "dummy", "portishead"),
    ("absolution by muse", "track", "absolution", "muse"),
    ("the seer by swans", "track", "the seer", "swans"),
]

# ---------------------------------------------------------------------------
# Genre / mood / playlist indicators
# ---------------------------------------------------------------------------

PLAYLIST_CASES = [
    ("some darkwave", "playlist"),
    ("chill jazz music", "playlist"),
    ("80s synthwave", "playlist"),
    ("ambient electronic", "playlist"),
    ("workout music", "playlist"),
    ("lo-fi hip hop", "playlist"),
    ("something relaxing", "playlist"),
    ("gothic rock", "playlist"),
    ("industrial music", "playlist"),
    ("play some trip-hop", "playlist"),
    ("post-punk vibes", "playlist"),
    ("90s alternative", "playlist"),
    ("dream pop", "playlist"),
    ("darkwave playlist", "playlist"),
    ("shoegaze", "playlist"),
    ("witch house mix", "playlist"),
]

# ---------------------------------------------------------------------------
# Bare names (no "by", no genre keywords) → should be "auto"
# The fallback chain (artist → playlist → album → tracks) handles these
# ---------------------------------------------------------------------------

AUTO_CASES = [
    "the police",
    "radiohead",
    "daft punk",
    "led zeppelin",
    "massive attack",
    "depeche mode",
    "nine inch nails",
    "the cure",
    "tool",
    "heilung",
    "wardruna",
    "sidewalks and skeletons",
    "californication",
    "dark side of the moon",
    "ok computer",
    "lateralus",
    "the downward spiral",
    "mezzanine",
    "bohemian rhapsody",
    "enjoy the silence",
    "smells like teen spirit",
    "where is my mind",
]

# ---------------------------------------------------------------------------
# Add your obscure/weird ones below
# ---------------------------------------------------------------------------

OBSCURE_CASES = [
    # Format: (query, expected_type) or (query, expected_type, expected_query, expected_artist)
    # ("igorrr", "auto"),
    # ("aenima by tool", "track", "aenima", "tool"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMusicClassifier:

    @pytest.mark.parametrize("query,expected_type,expected_query,expected_artist",
                             BY_PATTERN_CASES,
                             ids=[c[0] for c in BY_PATTERN_CASES])
    def test_by_pattern(self, classifier, query, expected_type, expected_query, expected_artist):
        result = classifier._classify_music_query(query)
        assert result["type"] == expected_type, f"'{query}' → type '{result['type']}', expected '{expected_type}'"
        assert expected_query.lower() in result["query"].lower(), (
            f"'{query}' → query '{result['query']}', expected to contain '{expected_query}'"
        )
        assert expected_artist.lower() in result.get("artist", "").lower(), (
            f"'{query}' → artist '{result.get('artist', '')}', expected to contain '{expected_artist}'"
        )

    @pytest.mark.parametrize("query,expected_type", PLAYLIST_CASES,
                             ids=[c[0] for c in PLAYLIST_CASES])
    def test_playlist(self, classifier, query, expected_type):
        result = classifier._classify_music_query(query)
        assert result["type"] == expected_type, (
            f"'{query}' → type '{result['type']}', expected '{expected_type}' "
            f"(query='{result.get('query', '?')}')"
        )

    @pytest.mark.parametrize("query", AUTO_CASES, ids=AUTO_CASES)
    def test_auto_fallback(self, classifier, query):
        """Bare names with no genre keywords or 'by' pattern should be 'auto',
        letting the Spotify search fallback chain decide."""
        result = classifier._classify_music_query(query)
        assert result["type"] == "auto", (
            f"'{query}' → type '{result['type']}', expected 'auto' "
            f"(query='{result.get('query', '?')}')"
        )

    @pytest.mark.parametrize("query,expected_type", [(c[0], c[1]) for c in OBSCURE_CASES] if OBSCURE_CASES else [("skip", "skip")],
                             ids=[c[0] for c in OBSCURE_CASES] if OBSCURE_CASES else ["no_obscure_cases"])
    def test_obscure(self, classifier, query, expected_type):
        if query == "skip":
            pytest.skip("No obscure cases defined yet")
        result = classifier._classify_music_query(query)
        assert result["type"] == expected_type, (
            f"'{query}' → type '{result['type']}', expected '{expected_type}'"
        )
