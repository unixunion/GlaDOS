"""Run this script to authenticate with Spotify. Only needed once — the token is cached.

Setup:
1. Go to https://developer.spotify.com/dashboard
2. Create an app (or use existing)
3. Add redirect URI: http://127.0.0.1:8888/callback  (http, NOT https)
4. Run this script — it starts a local server, opens browser, catches the callback automatically
"""
import os
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import spotipy
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID", "e85e1999f6964f07995660d744f9cd20")
CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET", "902d7a967dfa4cc29f16501a24f71424")
REDIRECT_URI = "https://127.0.0.1:8888/callback"
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
