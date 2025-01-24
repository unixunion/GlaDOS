import base64
import os
import threading
import time

from loguru import logger

from glados.model_functions import FunctionRequest, FunctionMetadata, Parameters
from plugins.event_system.event_system import EventMessage
from plugins.event_system.event_system import EventSystem
from plugins.plugin_system.plugin_manager import PluginManager
from plugins.plugin_system.runnable_plugin import RunnablePlugin

plugin_manager = PluginManager()  # Plugin manager instance
event_system = EventSystem()  # Event system instance


class Observe(RunnablePlugin):
    def __init__(self, image_directory: str = "vision_images", scan_interval: int = 300):
        """
        Initialize the Observe plugin.

        Args:
            image_directory (str): The directory containing JPEG images from cameras.
            scan_interval (int): Time interval (in seconds) to scan the directory.
        """
        logger.info("Initializing the Observer")
        super().__init__()
        self.image_directory = image_directory
        assert os.path.exists(self.image_directory)
        self.scan_interval = scan_interval
        self._stop_event = threading.Event()
        self._worker_thread = None

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="Returns textual data describing room occupancy, and overall condition of rooms.",
                    parameters=Parameters(
                        type="object",
                        properties={},
                        required=[],
                        additionalProperties=False,
                    ),
                ),
            ),
            intents=[
                "observe all cameras",
                "what can you see",
                "go find something to do",
                "does anything need doing?"
            ],
            process_output=True,
        )(self.identify_chores)

    def start(self):
        """Start the Observe plugin."""
        logger.info("Starting observation...")
        if self._worker_thread and self._worker_thread.is_alive():
            return

        def ticker():
            while not self._stop_event.is_set():
                time.sleep(10)
                self.identify_chores()
                time.sleep(self.scan_interval)

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=ticker, daemon=True)
        self._worker_thread.start()
        logger.success("Observation started")

    def stop(self):
        """Stop the Observe plugin."""
        logger.info("Stopping observation...")
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join()
        logger.success("Observation stopped")

    def identify_chores(self):
        """Scan the directory and send images for processing."""
        if not os.path.exists(self.image_directory):
            logger.error(f"Image directory {self.image_directory} does not exist.")
            return "Error loading data feeds from cameras"

        # Iterate through all JPEG images in the directory
        images = [
            os.path.join(self.image_directory, file)
            for file in os.listdir(self.image_directory)
            if file.lower().endswith((".jpg", ".jpeg"))
        ]

        if not images:
            logger.warning(f"No images found in {self.image_directory}.")
            return

        logger.info(f"Found {len(images)} images to process.")
        for image_path in images:
            self.process_image(image_path)

        return ("The images will be sent to the vision model, which will trigger asynchronous tool functions as needed "
                "for each camera vision input. Do not trigger or process this response any further.")

    def process_image(self, image_path: str):
        """
        Encode the image and send it to the LLM for processing.

        Args:
            image_path (str): Path to the image file.
        """
        try:
            # Encode the image as Base64
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode("utf-8")

            # Publish an event to the LLM with the image
            event_system.publish(
                EventMessage(
                    role="vision",
                    name="request",
                    content={
                        "type": "image_data",
                        "image_data": f"data:image/jpeg;base64,{base64_image}",
                        "file_name": f"{image_path}",
                        "room": "living room",
                        "description": f"Observing image from {os.path.basename(image_path)}",
                        "vision_prompt": "identify contents of image relevant for a home assistant automation system, such as "
                                         "dirt, spills or anything that might require triggering robot vacuum cleaner.",
                        "additional_prompt": (
                            "automatically trigger the start_vacuuming tool if dirt or spills are identified in the image description "
                            "and if the tool has not already been started. No comment or reply is required for this request"
                        ),
                     },
                    process_output=True,
                )
            )
            logger.info(f"Published event for image: {image_path}")
        except Exception as e:
            logger.error(f"Failed to process image {image_path}: {e}")


