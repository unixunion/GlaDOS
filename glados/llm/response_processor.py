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
        self.full_response = ""  # Accumulates the complete response for message history
        self.message_callback = message_callback  # Callback for finalized sentences
        self.client_type = client_type
        self._inside_think_block = False  # Track whether we're inside [THINK]...[/THINK]
        self._think_buffer = ""  # Buffer for partial tag detection at chunk boundaries

    def process_chunk(self, chunk: ChatCompletionChunk | AIMessageChunk):
        """
        Append content from the chunk to the current sentence. Finalize the sentence if it ends with punctuation.
        """

        if self.client_type is ClientType.OPENAI:
            if not chunk.choices[0].delta.content:
                return
            content = chunk.choices[0].delta.content
        elif self.client_type is ClientType.LANGCHAIN:
            if not chunk.content:
                return
            content = chunk.content
        else:
            return

        # Strip [THINK]...[/THINK] blocks so they aren't sent to TTS.
        # These arrive as streamed tokens, so we track state across chunks.
        content = self._strip_think_tags(content)
        if not content:
            return

        self.current_sentence += content
        logger.debug(f"Appended chunk: {content}")

        logger.debug(f"Current sentence: {self.current_sentence}")

        # Check if the sentence ends with a punctuation mark
        if re.search(r"[.!?]$", self.current_sentence.strip()):
            self.finalize_sentence()

    # All think tag patterns to detect (case-insensitive matching via upper())
    _OPEN_TAGS = ["[THINK]", "<THINK>"]
    _CLOSE_TAGS = ["[/THINK]", "</THINK>"]
    # Max length of any tag, used for buffering partial tags at chunk boundaries
    _MAX_TAG_LEN = max(len(t) for t in _OPEN_TAGS + _CLOSE_TAGS)

    def _strip_think_tags(self, text: str) -> str:
        """Strip think-tagged content from streamed text.

        Supports [THINK]...[/THINK] and <think>...</think> variants.
        Handles tags split across chunk boundaries by buffering potential
        partial tags at the end of each chunk.
        """
        # Prepend any buffered text from the previous chunk
        if self._think_buffer:
            text = self._think_buffer + text
            self._think_buffer = ""

        result = []
        i = 0
        upper_text = text.upper()

        while i < len(text):
            if self._inside_think_block:
                # Find the earliest closing tag
                best_end = -1
                best_tag_len = 0
                for tag in self._CLOSE_TAGS:
                    pos = upper_text.find(tag, i)
                    if pos != -1 and (best_end == -1 or pos < best_end):
                        best_end = pos
                        best_tag_len = len(tag)

                if best_end != -1:
                    stripped = text[i:best_end].strip()
                    if stripped:
                        logger.info(f"Stripped think content: {stripped[:120]}{'...' if len(stripped) > 120 else ''}")
                    self._inside_think_block = False
                    i = best_end + best_tag_len
                else:
                    # Still inside think block, discard rest
                    break
            else:
                # Find the earliest opening tag
                best_start = -1
                best_tag_len = 0
                for tag in self._OPEN_TAGS:
                    pos = upper_text.find(tag, i)
                    if pos != -1 and (best_start == -1 or pos < best_start):
                        best_start = pos
                        best_tag_len = len(tag)

                if best_start != -1:
                    result.append(text[i:best_start])
                    logger.info("Model entered think block — stripping reasoning")
                    self._inside_think_block = True
                    i = best_start + best_tag_len
                else:
                    # No complete tag found — but the tail of the text might
                    # be the start of a tag split across chunks (e.g. "[THI")
                    safe_end = len(text)
                    if not self._inside_think_block:
                        # Check if the tail could be the start of any tag
                        for look_back in range(1, min(self._MAX_TAG_LEN, len(text) - i)):
                            tail = upper_text[len(text) - look_back:]
                            if any(tag.startswith(tail) for tag in self._OPEN_TAGS + self._CLOSE_TAGS):
                                safe_end = len(text) - look_back
                                self._think_buffer = text[safe_end:]
                                break
                    result.append(text[i:safe_end])
                    break

        return "".join(result)

    def _is_meta_commentary(self, sentence: str) -> bool:
        """No-op. Think tag stripping is handled by _strip_think_tags().
        Use a proper instruct model that doesn't leak reasoning."""
        return False

    def finalize_sentence(self):
        """
        Finalize the current sentence: send it to the TTS queue.
        The full response is accumulated and stored in message history
        only once via finalize_response().
        """
        sentence = self.current_sentence.strip()
        if sentence:
            # Skip meta-commentary / untagged chain-of-thought
            if self._is_meta_commentary(sentence):
                logger.info(f"Stripping meta-commentary: {sentence[:80]}...")
                self.current_sentence = ""
                return

            logger.debug(f"Finalizing sentence: {sentence}")

            # Accumulate into the full response for later storage
            if self.full_response:
                self.full_response += " " + sentence
            else:
                self.full_response = sentence

            # Send to TTS queue
            if self.tts_queue:
                self.tts_queue.put(sentence)
            else:
                logger.warning("TTS queue is not set. Sentence will not be processed.")
        else:
            logger.debug("Finalize called with empty sentence (normal after tool calls)")

        # Reset current_sentence after processing
        self.current_sentence = ""

    def finalize_response(self):
        """Store the complete accumulated response in message history as a single message."""
        response = self.full_response.strip()
        if response:
            logger.debug(f"Storing complete assistant response ({len(response)} chars)")
            if self.message_callback:
                self.message_callback(response)
        self.full_response = ""


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
