"""Standalone Spotify auth script (alternative to the web UI method).

Preferred method: Open http://localhost:5001/spotify/auth in a browser while GlaDOS is running.

This script is for headless setups or when GlaDOS isn't running.

Setup:
1. Go to https://developer.spotify.com/dashboard
2. Create an app (or use existing)
3. Add BOTH redirect URIs:
   - http://localhost:5001/spotify/callback  (for web UI auth)
   - http://127.0.0.1:8888/callback          (for this script)
4. Set env vars: SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET
5. Run this script
"""
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import spotipy
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET", "")
if not CLIENT_ID or not CLIENT_SECRET:
    print("Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET environment variables.")
    print("Get them from https://developer.spotify.com/dashboard")
    sys.exit(1)
REDIRECT_URI = "http://127.0.0.1:5001/spotify/callback"
SCOPE = "user-modify-playback-state user-read-playback-state user-read-currently-playing"
CACHE_PATH = ".spotify_cache"

auth_code = None
server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)
        if "code" in query:
            auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authenticated! You can close this tab.</h1></body></html>")
            server_done.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter")

    def log_message(self, format, *args):
        pass


# Start local HTTP server to catch the redirect
server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()

auth_manager = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path=CACHE_PATH,
)

auth_url = auth_manager.get_authorize_url()
print(f"Opening browser for Spotify authorization...")
print(f"\nAuth URL: {auth_url}\n")
webbrowser.open(auth_url)

print("Waiting for authorization...")
server_done.wait(timeout=120)
server.shutdown()

if not auth_code:
    print("ERROR: Timed out waiting for authorization.")
    exit(1)

token_info = auth_manager.get_access_token(auth_code)
print(f"\nToken cached to {CACHE_PATH}")

sp = spotipy.Spotify(auth_manager=auth_manager)
print("Authenticated successfully!\n")

devices = sp.devices().get("devices", [])
if devices:
    print("Spotify devices:")
    for d in devices:
        active = "[ACTIVE]" if d["is_active"] else ""
        print(f"  - {d['name']} ({d['type']}) {active}")
else:
    print("No Spotify devices found. Open Spotify on a device first.")
