import base64
import datetime
import os
import threading
import time

from loguru import logger

from glados.context.activity import Activity
from glados.system.function_calling import FunctionRequest, FunctionMetadata, Parameters, ParameterType
from glados.rate_limit import rate_limited
from glados.system.event_system import EventMessage
from glados.system.event_system import EventSystem
from glados.system.plugin import PluginSystem
from glados.system.runnable_plugin import RunnablePlugin

plugin_manager = PluginSystem()  # Plugin manager instance
event_system = EventSystem()  # Event system instance


class Observe(RunnablePlugin):
    def __init__(self, image_directory: str = "vision_images", scan_interval: int = 900):
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
        self._last_called = datetime.datetime.now()

        # plugin_manager.register(
        #     llm_function_request=FunctionRequest(
        #         type="function",
        #         function=FunctionMetadata(
        #             description="Returns textual data describing room occupancy, and overall condition of rooms.",
        #             parameters=Parameters(
        #                 type="object",
        #                 properties={},
        #                 required=[],
        #                 additionalProperties=False,
        #             ),
        #         ),
        #     ),
        #     intents=[
        #         "observe all cameras",
        #         "what can you see",
        #         "go find something to do",
        #         "does anything need doing?"
        #     ],
        #     process_output=True,
        # )(self.identify_chores)

        plugin_manager.register(
            llm_function_request=FunctionRequest(
                type="function",
                function=FunctionMetadata(
                    description="This function observes the various rooms of the household, or can be used to describe an "
                                "object in any room for general purposes, such as determining a response relevant to a "
                                "request which relates to a specific room or person.",
                    parameters=Parameters(type="object", required=['query'], properties={
                        'room': ParameterType(
                            type="string",
                            description="The room in which to make the observation.",
                            enum=["OFFICE", "CLASSROOM", "KITCHEN", "HALL", "LIVING_ROOM", "BEDROOM", "DINING_ROOM"],
                        ),
                        'query': ParameterType(
                            type="string",
                            description="The observation related to the room that is to be made.",
                        )
                    })
                ),
            ),
            intents=[
                "what is going on in the office",
                "which room's are occupied",
                "what is happening in the kitchen",
                "anything to remark on in the dining room",
                "I am in the hall, what am I holding in my hand",
                "help locate my keys",
                "what is mira doing",
                "scan the classroom for any visible items",
                "observe the bedroom for movement",
                "check the living room for people",
                "are there any objects of interest in the dining room",
                "find anything misplaced in the office",
                "report activity in the hallway",
            ],
            process_output=True,
            activity=[Activity.GENERAL]
        )(self.get_camera_feed)

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
        logger.info("Looking for chores based on observations")
        if not os.path.exists(self.image_directory):
            logger.error(f"Image directory {self.image_directory} does not exist.")
            return "Error loading data feeds from cameras"

        # Iterate through all JPEG images in the directory, skipping stale/cached images
        now = time.time()
        images = []
        for file in os.listdir(self.image_directory):
            if not file.lower().endswith((".jpg", ".jpeg")):
                continue
            path = os.path.join(self.image_directory, file)
            age = now - os.path.getmtime(path)
            if age > self.scan_interval:
                logger.debug(f"Skipping stale image {file} (age: {age:.0f}s)")
                continue
            images.append(path)

        if not images:
            logger.debug(f"No fresh images found in {self.image_directory}.")
            return

        logger.info(f"Found {len(images)} fresh images to process.")
        for image_path in images:
            self.process_image(image_path,
                               vision_prompt="Check the image and identify areas that need cleaning, such as floor vacuuming "
                                             "spills, dirt, or other untidy sitations. ",
                               additional_prompt="Use the room description should be evaluated to determine if the "
                                                 "robot vacuum cleaner needs to run. Respond only with '(silence)'"
                               )

        return "The images will be accessed for chores, respond only with '(silence)'"

    @rate_limited(600)
    def get_camera_feed(self, room: str = None, query: str = None) -> dict:
        """Scan the directory and send images for processing. and then observes """

        logger.info(f"Performing observations for room: {room if room else 'ALL ROOMS'}")

        if query:
            query = (f"Describe what is observed within the context: {query}")
            logger.info(f"Query: {query}")

        if not os.path.exists(self.image_directory):
            logger.error(f"Image directory {self.image_directory} does not exist.")
            return {"status": "Error: Unable to access the vision images repository. Please ensure the directory exists and is accessible."}

        # Iterate through all JPEG images in the directory
        images = [
            os.path.join(self.image_directory, file)
            for file in os.listdir(self.image_directory)
            if file.lower().endswith((".jpg", ".jpeg"))
               and (not room or file.lower().startswith(room.lower()))  # Filter by room prefix if provided
        ]

        if not images:
            logger.warning(f"No images found in {self.image_directory} matching room: {room if room else 'ALL ROOMS'}.")
            return {"status": f"No vision data found for room: {room if room else 'ALL ROOMS'}"}

        logger.info(f"Found {len(images)} images to process for room: {room if room else 'ALL ROOMS'}.")
        for image_path in images:
            try:
                self.process_image(image_path,
                                   vision_prompt=query or "Identify and describe the contents of the image "
                                                          "relevant to observing the state of a room,"
                                                          "such as the presence of people, objects, items out of "
                                                          "place, or any unusual activities. Focus on details that "
                                                          "might indicate actions or responses needed within the room.",
                                   additional_prompt="Descibe in detail what is observed in the description in relation to "
                                                     f"the request: '{query or None}'. Invoke any functions that seem relevant to the enquiry.",
                                   )
            except Exception as e:
                logger.error(f"Error processing image {image_path}: {e}")

        return {"status": "pending, the tool will call back with the information when it is ready"}

    def process_image(self, image_path: str, vision_prompt: str = None, additional_prompt: str = None):
        """
        Encode the image and send it to the LLM for processing.

        Args:
            vision_prompt: the prompt to the vision model
            additional_prompt: the prompt back to the llm when interpreting vision model description
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
                        "room": f"{image_path}",
                        "description": f"Observing image from {os.path.basename(image_path)}",
                        "vision_prompt": vision_prompt or "identify contents of image relevant for a home assistant automation system, such as "
                                                          "observations, dirt, spills or anything that might require triggering robot vacuum cleaner. ",
                        "additional_prompt": (
                                additional_prompt or "automatically trigger the start_vacuuming tool if dirt or spills are identified in the image description "
                                                     "and if the tool has not already been started. No comment or reply is required for this request"
                        ),
                    },
                    process_output=True,
                )
            )
            logger.info(f"Published event for image: {image_path}")
        except Exception as e:
            logger.error(f"Failed to process image {image_path}: {e}")
