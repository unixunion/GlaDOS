import sys
from loguru import logger

# logger.remove()
# logger.add(sys.stderr, level="DEBUG")

import base64
import io
import os
import time

from PIL import Image
from flask import Flask, make_response
from flask_socketio import SocketIO

"""
This service serves a static HTML page that allows remote devices to send their camera video feeds.
If a cookie is not set, the user can label the camera. The service saves a frame from the video stream
every 60 seconds in JPEG format into a specific directory.
"""

# HTML content with cookie check for labeling
html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Camera Stream</title>
</head>
<body>
    <h1>Camera Stream to Server</h1>
    <div id="label-container" style="display: none;">
        <label for="camera-label">Label your camera:</label>
        <input type="text" id="camera-label" placeholder="Enter camera label" />
        <button onclick="setLabel()">Start</button>
    </div>
    <video id="videoElement" autoplay muted style="display: none;"></video>
    <script>
        const videoElement = document.getElementById("videoElement");
        const labelContainer = document.getElementById("label-container");

        // Check for camera label cookie
        const cameraLabel = document.cookie.split('; ').find(row => row.startsWith('cameraLabel='));
        if (!cameraLabel) {
            labelContainer.style.display = "block";
        } else {
            startStream();
        }

        // Function to set camera label in cookie
        function setLabel() {
            const label = document.getElementById("camera-label").value;
            if (label) {
                document.cookie = `cameraLabel=${label}; path=/;`;
                labelContainer.style.display = "none";
                startStream();
            }
        }

        // Function to start video stream
        function startStream() {
            videoElement.style.display = "block";
            navigator.mediaDevices.getUserMedia({ video: true, audio: false })
                .then(stream => {
                    const canvas = document.createElement('canvas');
                    const videoElement = document.getElementById('videoElement');
                    videoElement.srcObject = stream;
            
                    const socket = io();
            
                    // Periodically capture frames from the video and send as JPEG
                    setInterval(() => {
                        canvas.width = videoElement.videoWidth;
                        canvas.height = videoElement.videoHeight;
            
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
            
                        // Convert to JPEG Base64
                        const dataURL = canvas.toDataURL('image/jpeg');
                        const base64data = dataURL.split(',')[1]; // Strip `data:image/jpeg;base64,`
                        socket.emit('frame', { label: getCookie('cameraLabel'), frame: base64data });
                    }, 1000); // Send a frame every second
                })
                .catch(err => console.error('Error accessing camera: ', err));
            
        }

        // Helper to get cookie value
        function getCookie(name) {
            const value = `; ${document.cookie}`;
            const parts = value.split(`; ${name}=`);
            if (parts.length === 2) return parts.pop().split(";").shift();
        }
    </script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
</body>
</html>
"""

# Flask app setup
app = Flask(__name__)
socketio = SocketIO(app)

# Directory to save frames
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Save frames every 60 seconds
LAST_SAVED = {}


# Serve the HTML page
@app.route("/")
def index():
    response = make_response(html)
    response.headers["Content-Type"] = "text/html"
    return response


# Handle incoming frames
@socketio.on("frame")
def handle_frame(data):
    global LAST_SAVED
    label = data.get("label", "unknown_camera")
    frame_data = data.get("frame")

    if not frame_data:
        print("No frame data received.")
        return

    logger.debug(f"Label: {label}")
    logger.debug(f"Frame Data Type: {type(frame_data)}")
    logger.debug(f"Frame Data Length: {len(frame_data)}")
    logger.debug(f"Frame Data (First 100 chars): {frame_data[:100]}")

    try:
        # Decode Base64 string
        frame_bytes = base64.b64decode(frame_data)

        # Open the image
        image = Image.open(io.BytesIO(frame_bytes))
        image = image.convert("RGB")  # Ensure it's in RGB mode

        # Save a frame every 60 seconds
        current_time = time.time()
        if label not in LAST_SAVED or current_time - LAST_SAVED[label] >= 60:
            file_path = os.path.join(UPLOAD_DIR, f"{label}_{int(current_time)}.jpeg")
            image.save(file_path, format="JPEG")
            LAST_SAVED[label] = current_time
            print(f"Saved frame for {label} at {file_path}")
    except Exception as e:
        logger.exception(f"Error processing frame: {e}")


if __name__ == "__main__":
    print("Starting server on http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
