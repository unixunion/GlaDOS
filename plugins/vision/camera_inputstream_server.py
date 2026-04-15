import threading
import time
import base64
import io
import os
from PIL import Image
from flask import Flask, make_response
from flask_socketio import SocketIO
from loguru import logger

from glados.system.event_system import EventSystem, EventMessage
from glados.system.runnable_plugin import RunnablePlugin

# HTML content served to the browser
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Camera Stream</title>
</head>
<body>
    <h1>Camera Stream to GlaDOS</h1>
    <div id="label-container" style="display: none;">
        <label for="camera-label">Label your camera:</label>
        <input type="text" id="camera-label" placeholder="Enter camera label" />
        <button onclick="setLabel()">Start</button>
    </div>
    <video id="videoElement" autoplay muted style="display: none;"></video>
    <script>
        const videoElement = document.getElementById("videoElement");
        const labelContainer = document.getElementById("label-container");

        const cameraLabel = document.cookie.split('; ').find(row => row.startsWith('cameraLabel='));
        if (!cameraLabel) {
            labelContainer.style.display = "block";
        } else {
            startStream();
        }

        function setLabel() {
            const label = document.getElementById("camera-label").value;
            if (label) {
                document.cookie = `cameraLabel=${label}; path=/;`;
                labelContainer.style.display = "none";
                startStream();
            }
        }

        function startStream() {
            videoElement.style.display = "block";
            navigator.mediaDevices.getUserMedia({ video: true, audio: false })
                .then(stream => {
                    const canvas = document.createElement('canvas');
                    videoElement.srcObject = stream;

                    const socket = io();
                    setInterval(() => {
                        canvas.width = videoElement.videoWidth;
                        canvas.height = videoElement.videoHeight;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

                        const dataURL = canvas.toDataURL('image/jpeg');
                        const base64data = dataURL.split(',')[1];
                        socket.emit('frame', { label: getCookie('cameraLabel'), frame: base64data });
                    }, 1000);
                })
                .catch(err => console.error('Error accessing camera: ', err));
        }

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

UPLOAD_DIR = "vision_images"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class CameraInputStreamPlugin(RunnablePlugin):
    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._flask_app = Flask(__name__)
        self._socketio = SocketIO(self._flask_app)
        self._last_saved = {}
        self._configure_routes()
        self.event_system = EventSystem()

    def _configure_routes(self):
        # Serve the HTML page
        @self._flask_app.route("/")
        def index():
            response = make_response(HTML)
            response.headers["Content-Type"] = "text/html"
            return response

        # Handle incoming frames
        @self._socketio.on("frame")
        def handle_frame(data):
            label = data.get("label", "unknown_camera")
            frame_data = data.get("frame")

            if not frame_data:
                logger.warning("No frame data received.")
                return

            logger.debug(f"Label: {label}")
            logger.debug(f"Frame Data Length: {len(frame_data)}")

            try:
                frame_bytes = base64.b64decode(frame_data)
                image = Image.open(io.BytesIO(frame_bytes))
                image = image.convert("RGB")

                current_time = time.time()
                if label not in self._last_saved or current_time - self._last_saved[label] >= 1:
                    file_path = os.path.join(UPLOAD_DIR, f"{label}.jpeg")
                    image.save(file_path, format="JPEG")
                    self._last_saved[label] = current_time
                    logger.debug(f"Saved frame for {label} at {file_path}")
            except Exception as e:
                logger.exception(f"Error processing frame: {e}")

    def start(self):
        logger.info("Starting CameraStreamPlugin...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        # Port 5000 clashes with macOS AirPlay Receiver, so use 5002
        # (display server uses 5001).
        port = 5002

        def run_flask():
            logger.info(f"Calling socketio run on port {port}...")
            self._socketio.run(self._flask_app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=run_flask, daemon=True)
        self._worker_thread.start()
        logger.success("CameraStreamPlugin started!")
        self.event_system.publish(EventMessage(
            role="tool",
            name="get_camera_feed",
            content="The camera system is now collecting images from camera's",
            process_output=False
        ))

    def stop(self):
        logger.info("Stopping CameraStreamPlugin...")
        self._stop_event.set()
        logger.success("CameraStreamPlugin stopped!")
