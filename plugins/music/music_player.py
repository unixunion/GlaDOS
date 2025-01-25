import os
import sounddevice as sd
from loguru import logger
from pydub import AudioSegment
import numpy as np
from fuzzywuzzy import process
from glados.system.plugin import PluginSystem

plugin_manager = PluginSystem()

MUSIC_DIR = "F:\mp3"  # Replace with the path to your music folder
CURRENT_TRACK = None  # To keep track of the currently playing track
IS_PLAYING = False  # To control playback state


# @plugin_manager.register(
#     llm_function_request=FunctionRequest(
#         type="function",
#         function=FunctionMetadata(
#             description="Controls music playback.",
#             parameters=Parameters(type="object", required=['query', 'action'], properties={
#                 'query': ParameterType(type="string", description="The song, artist, or album to play."),
#                 'action': ParameterType(
#                     type="string",
#                     description="The music player control to invoke, such as switching tracks, starting, stopping, "
#                                 "pausing, and so forth.",
#                     enum=["PLAY", "STOP", "NEXT_TRACK", "PREVIOUS_TRACK"],
#                 )
#             })
#         )
#     ),
#     intents=[
#         "Play ben howard",
#         "play the album spice",
#         "stop playing",
#         "stop the music",
#         "play the song teen spirit by nirvana",
#         "play my chemical romance",
#         "next track",
#         "play the album walls by kings of leon",
#         "next song",
#         "previous track",
#         "previous song",
#         "pause the music"
#         "pause",
#         "skip track",
#         "skip song"
#     ]
# )
def play_music(query: str, action: str):
    """
    Given a query, locate a song matching this in the os.path for the music files,
    it needs to fuzzy match the query, and use the action to switch on what to do.
    """
    global CURRENT_TRACK, IS_PLAYING

    def find_song(query):
        """Locate the best match for the song in the music directory, including subdirectories."""
        files = []
        for root, _, filenames in os.walk(MUSIC_DIR):
            for filename in filenames:
                if filename.lower().endswith(".mp3"):
                    files.append(os.path.join(root, filename))

        if not files:
            raise FileNotFoundError("No MP3 files found in the music directory.")

        # Extract only the filename for fuzzy matching
        filenames = [os.path.relpath(file, MUSIC_DIR) for file in files]
        best_match = process.extractOne(query, filenames)
        if best_match:
            logger.info(f"Best match: {best_match}, returning: {files[filenames.index(best_match[0])]}")
            return files[filenames.index(best_match[0])]
        return None

    if action == "PLAY":
        if IS_PLAYING:
            print("Music is already playing.")
            return

        file_path = find_song(query)
        if not file_path:
            print(f"No match found for '{query}'.")
            return

        CURRENT_TRACK = file_path
        print(f"Playing: {CURRENT_TRACK}")

        audio = AudioSegment.from_file(file_path, format="mp3")

        # Convert to numpy array for sounddevice
        samples = np.array(audio.get_array_of_samples()).astype(np.float32) / (2 ** 15)  # Normalize to -1 to 1

        # Handle stereo/mono
        if audio.channels == 2:
            samples = samples.reshape((-1, 2))

        # Play the audio
        IS_PLAYING = True
        sd.play(samples, samplerate=audio.frame_rate)
        sd.wait()  # Wait until playback finishes
        IS_PLAYING = False

    elif action == "STOP":
        if not IS_PLAYING:
            print("No music is currently playing.")
            return
        sd.stop()
        IS_PLAYING = False
        print("Playback stopped.")

    elif action in ["NEXT_TRACK", "PREVIOUS_TRACK"]:
        print(f"Action '{action}' is not implemented yet.")

    else:
        print(f"Unknown action: {action}")
