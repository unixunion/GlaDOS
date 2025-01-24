import queue
import threading

from loguru import logger
from openai import OpenAI
from openai.types.chat import ChatCompletion

from glados.config import GladosConfig
from plugins.event_system.event_system import EventMessage, EventSystem, EventHook


class VisionClient:
    def __init__(self, config: GladosConfig):
        if not config.vision_model or not config.vision_completion_url:
            logger.warning("Vision system disabled")
            return
        self.event_system = EventSystem()
        self.model = config.vision_model or ValueError("You need to provide a vision model")
        self.request_topic = "vision.request"
        self.client = OpenAI(base_url=config.vision_completion_url, api_key=config.api_key)
        self.processing_request = False

        self.image_queue: dict = {}
        self.queue_lock = threading.Lock()

        # Subscribe to vision requests
        logger.info(f"Subscribing to {self.request_topic}")
        self.event_system.subscribe(
            self.request_topic,
            EventHook(name="vision_request_handler", callback=self.queue_vision_request)
        )

        # Start a thread to process the queue
        self.queue_thread = threading.Thread(target=self.process_image_queue, daemon=True)
        self.queue_thread.start()

    def queue_vision_request(self, event: EventMessage):
        """Queue vision request keyed by file_name."""
        file_name = event.content.get("file_name", None)
        if file_name:
            logger.info(f"Queueing request for file: {file_name}")
            with self.queue_lock:
                self.image_queue[file_name] = event
        else:
            logger.warning("Event missing file_name, skipping.")

    def process_image_queue(self):
        """Continuously process items in the queue."""
        while True:
            file_name, event = None, None

            # Safely pop an item from the queue
            with self.queue_lock:
                if self.image_queue:
                    file_name, event = self.image_queue.popitem()

            # If there is an item to process, handle it
            if file_name and event:
                logger.info(f"Processing image request for file: {file_name}")
                self.process_image_request(file_name, event.content)
            else:
                # If the queue is empty, wait for new items
                threading.Event().wait(0.1)

    def process_image_request(self, file_name: str, content: dict):
        """Process a single image request."""
        try:
            image_data = content.get("image_data")
            additional_prompt = content.get("additional_prompt", "no additional context was provided, determine the best course "
                                                                 "of action based on image description")
            vision_prompt = content.get("vision_prompt", "you are a vision assistant, describe to contents of this image")

            # Call the actual vision model for processing
            logger.info(f"Processing image for {file_name}")
            response: ChatCompletion = self._query_vision_model(image_data, vision_prompt)

            # Extract the relevant information from the response
            description = response.choices[0].message.content
            logger.info(f"Vision model response for {file_name}: {description}")

            # Publish the result as a vision.response event
            self.event_system.publish(EventMessage(
                role="vision",
                name="response",
                content={"file_name": file_name, "description": description, "prompt": additional_prompt}
            ))
        except Exception as e:
            logger.exception(f"Error processing image for {file_name}: {e}")

    def _query_vision_model(self, image_data: str, prompt: str) -> ChatCompletion:
        """
        Queries the vision model with the provided image and prompt.

        Args:
            image_data (str): The Base64-encoded image data.
            prompt (str): Additional prompt for the vision model.

        Returns:
            dict: Response from the vision model.
        """
        # Ensure the image is Base64-encoded
        if not image_data.startswith("data:image"):
            raise ValueError("Image data must be Base64-encoded.")

        logger.info(f"Calling the OpenAI API for vision processing... {self.model}")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system",
                 "content": "You are a vision assistant for a home assistant automation. Describe the content of the images provided "
                            "in the context of household chores, such as floor cleaning, room occupancy, pet occupancy, light "
                            "statuses such as on or off in a given room. Then summarize this data in a format suitable for "
                            "analyses by a LLM text model"},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data}}
                ]}
            ],
            temperature=0.6
        )
        logger.success("Received response from vision model")
        return response
