"""Paste the redirect URL from the browser to exchange for a token."""
import sys
import spotipy
from spotipy.oauth2 import SpotifyOAuth

auth_manager = SpotifyOAuth(
    client_id="e85e1999f6964f07995660d744f9cd20",
    client_secret="902d7a967dfa4cc29f16501a24f71424",
    redirect_uri="https://127.0.0.1:8888/callback",
    scope="user-modify-playback-state user-read-playback-state user-read-currently-playing",
    cache_path=".spotify_cache",
)

url = input("Paste the full redirect URL from the browser: ").strip()
code = auth_manager.parse_response_code(url)
token_info = auth_manager.get_access_token(code)
print("\nToken cached to .spotify_cache!")

sp = spotipy.Spotify(auth_manager=auth_manager)
devices = sp.devices().get("devices", [])
if devices:
    print("\nSpotify devices:")
    for d in devices:
        active = "[ACTIVE]" if d["is_active"] else ""
        print(f"  - {d['name']} ({d['type']}) {active}")
else:
    print("\nNo Spotify devices found. Open Spotify on a device first.")
