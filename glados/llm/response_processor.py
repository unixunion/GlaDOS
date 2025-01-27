import queue
import re

from langchain_core.messages import AIMessageChunk, BaseMessageChunk
from loguru import logger
from openai.types.chat import ChatCompletionChunk

from glados.llm.client_type import ClientType


class ResponseProcessor:
    def __init__(self, tts_queue: queue.Queue = None, message_callback=None, client_type: ClientType = ClientType.OPENAI):
        """
        Initialize the ResponseProcessor.

        Args:
            tts_queue (queue.Queue): Queue for sending sentences to TTS.
            message_callback (callable): Callback to handle finalized sentences (e.g., for MessageManager).
        """
        self.tts_queue = tts_queue
        self.current_sentence = ""
        self.message_callback = message_callback  # Callback for finalized sentences
        self.client_type = client_type

    def process_chunk(self, chunk: ChatCompletionChunk | AIMessageChunk):
        """
        Append content from the chunk to the current sentence. Finalize the sentence if it ends with punctuation.
        """

        if self.client_type is ClientType.OPENAI:
            if not chunk.choices[0].delta.content:
                return
            self.current_sentence += chunk.choices[0].delta.content
            logger.debug(f"Appended chunk: {chunk.choices[0].delta.content}")

        if self.client_type is ClientType.LANGCHAIN:
            if not chunk.content:
                return
            self.current_sentence += chunk.content
            logger.debug(f"Appended chunk: {chunk.content}")

        logger.debug(f"Current sentence: {self.current_sentence}")

        # Check if the sentence ends with a punctuation mark
        if re.search(r"[.!?]$", self.current_sentence.strip()):
            self.finalize_sentence()

    def finalize_sentence(self):
        """
        Finalize the current sentence and send it to the TTS queue and the message manager.
        """
        sentence = self.current_sentence.strip()
        if sentence:
            logger.debug(f"Finalizing sentence: {sentence}")

            # Send to TTS queue
            if self.tts_queue:
                self.tts_queue.put(sentence)
            else:
                logger.warning("TTS queue is not set. Sentence will not be processed.")

            # Trigger the message callback
            if self.message_callback:
                logger.debug("Sending finalized sentence to message callback.")
                self.message_callback(sentence)
            else:
                logger.warning("Message callback is not set. Sentence will not be stored.")
        else:
            logger.warning("Attempted to finalize an empty sentence., this might not be a problem if there was nothing"
                           " left to process")

        # Reset current_sentence after processing
        self.current_sentence = ""


# import queue
# import re
#
# from loguru import logger
# from openai.types.chat import ChatCompletionChunk
#
#
# class ResponseProcessor:
#     def __init__(self, tts_queue: queue.Queue = None):
#         self.tts_queue = tts_queue
#         self.current_sentence = ""
#
#     def process_chunk(self, chunk: ChatCompletionChunk):
#         if not chunk.choices[0].delta.content:
#             return
#
#         self.current_sentence += chunk.choices[0].delta.content
#         if re.search(r"[.!?]$", self.current_sentence.strip()):
#             self.finalize_sentence()
#
#     def finalize_sentence(self):
#         sentence = self.current_sentence.strip()
#         if sentence:
#             logger.info(f"Processing sentence: {sentence}")
#             self.tts_queue.put(sentence)
#         self.current_sentence = ""
