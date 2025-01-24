import asyncio
import queue
import re
import threading

from loguru import logger
from num2words import num2words

from plugins.event_system.event_system import EventSystem, EventMessage



class GladosSpeechModule:
    def __init__(self, tts, tts_queue: asyncio.Queue, interruptible: bool = True, speaking_lock: threading.Event = None):
        """
        Initializes the SpeechModule for TTS processing.

        Args:
            tts: The TTS synthesizer instance.
            tts_queue: The queue containing text to be processed into speech.
            interruptible: Whether the speech playback can be interrupted by new input (deprecated).
            speaking_lock: threading.Event, locked while speaking.
        """
        self._tts = tts
        self._tts_queue = tts_queue
        self.interruptible = interruptible  # deprecated
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._process_queue, daemon=True)
        self.event_system = EventSystem()
        self._speaking_lock = speaking_lock or threading.Event()  # Ensure a default Event is created
        self.stt_enabled = True

    def start(self):
        """
        Start the speech processing thread.
        """
        logger.info("Starting SpeechModule...")
        self._stop_event.clear()
        self._thread.start()
        self._say("speech module activated")

    def stop(self):
        """
        Stop the speech processing thread.
        """
        logger.info("Stopping SpeechModule...")
        self._stop_event.set()
        self._thread.join()

    def _process_queue(self):
        """
        Continuously process text from the TTS queue and play audio.
        """
        while not self._stop_event.is_set():
            try:
                # Wait for text from the queue with a timeout
                generated_text = self._tts_queue.get(timeout=0.1)

                if generated_text == "<EOS>":  # End-of-stream signal
                    logger.info("Received end-of-stream signal. Clearing speaking lock.")
                    self._speaking_lock.clear()
                    logger.info("Consider sending event to listen for response for a brief period, TODO")
                    self.event_system.publish(EventMessage(
                        "system",
                        "listen_for_response",
                        {}
                    ))
                    continue

                if not generated_text:
                    logger.warning("Empty string received for TTS processing.")
                    continue

                logger.debug(f"Locking speaking thread for TTS: {generated_text}")
                self._speaking_lock.set()  # Lock the speaking event
                processed_text = self._process_text(generated_text)
                self._say(processed_text)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in SpeechModule: {e}")

    def _say(self, text: str):
        """
        Generate and play TTS audio from text.
        """
        try:
            logger.info(f"Generating TTS for: {text}")

            # Disable STT while TTS is playing
            if self.stt_enabled:
                # self.event_system.publish(EventMessage("system", "disable_stt", {}))
                self.stt_enabled = False

            # Generate TTS audio
            audio = self._tts.generate_speech_audio(text)
            self._play_audio(audio)

        except Exception as e:
            logger.error(f"Error during TTS playback: {e}")
        finally:
            # Re-enable STT after TTS completes
            if not self.stt_enabled:
                # self.event_system.publish(EventMessage("system", "enable_stt", {}))
                self.stt_enabled = True

            self._speaking_lock.clear()  # Clear the speaking lock
            logger.debug("TTS playback completed. Re-enabling STT.")

    def _play_audio(self, audio):
        """
        Play the generated TTS audio.
        """
        import sounddevice as sd
        try:
            logger.debug("Playing TTS audio...")
            sd.play(audio, self._tts.rate)
            sd.wait()  # Wait for playback to complete
        except Exception as e:
            logger.error(f"Error during audio playback: {e}")

    def _process_text(self, text: str) -> str:
        """
        Process text for better pronunciation and cleaner output for TTS.

        Args:
            text (str): The input text.

        Returns:
            str: The cleaned and processed text.
        """
        logger.debug(f"Original TTS text: {text}")

        # Replace numbers with words
        text = self._replace_numbers_with_words(text)

        # Filter out special tags
        text = self._filter_special_tags(text)

        # Normalize spacing and punctuation
        text = self._normalize_text(text)

        logger.debug(f"Processed TTS text: {text}")
        return text

    def _replace_numbers_with_words(self, text: str) -> str:
        """
        Replace numerical values in the text with their word equivalents,
        handling cases where numbers are part of words (e.g., "6pm" -> "six pm").
        """

        def number_to_words(match):
            number = match.group("number")
            prefix = match.group("prefix") or ""
            suffix = match.group("suffix") or ""
            # Convert the number to words
            number_word = num2words(int(number))
            # Reassemble the full word
            return f"{prefix}{number_word}{suffix}"

        # Regex to capture numbers with optional prefixes/suffixes (e.g., "6pm", "7:30")
        pattern = r"(?P<prefix>[^a-zA-Z\d])?(?P<number>\d+)(?P<suffix>[a-zA-Z]*)"

        return re.sub(pattern, number_to_words, text)

    # def _replace_numbers_with_words(self, text: str) -> str:
    #     """Replace numerical values in the text with their word equivalents."""
    #     def number_to_words(match):
    #         number = int(match.group())
    #         return num2words(number)
    #
    #     return re.sub(r'\b\d+\b', number_to_words, text)

    def _filter_special_tags(self, text: str) -> str:
        """Filter out special tags or metadata from the text."""
        filtered_text = re.sub(
            r"(_[A-Z_]+_[A-Z_]+(?:\[[^\]]*\])?)|START_TOKEN_[A-Z_]+|END_TOKEN_[A-Z_]+",
            "",
            text
        )
        return filtered_text.strip()

    def _normalize_text(self, text: str) -> str:
        """Normalize spacing, punctuation, and line breaks for cleaner TTS output."""
        # Fix pronunciation of some words
        text = re.sub(r"(?i)\bplugins\b", "plug-ins", text)  # Match whole words case-insensitively
        text = re.sub(r"(?i)\bplugin\b", "plug-in", text)  # Match whole words case-insensitively
        text = re.sub(r"(?i)\bglados\b", "gladys", text)  # Match 'glados' case-insensitively

        # Remove extra whitespace and normalize punctuation
        text = (
            text.replace("\n\n", ". ")
                .replace("\n", ". ")
                .replace("  ", " ")
                .replace(":", " ")
        )
        text = re.sub(r"\s{2,}", " ", text).strip()  # Collapse extra spaces
        return text






# class GladosSpeechModule:
#     def __init__(self, tts,
#                  tts_queue: asyncio.Queue,
#                  interruptible: bool = True,
#                  speaking_lock: threading.Event = None):
#         """
#         Initializes the SpeechModule for TTS processing.
#
#         Args:
#             tts: The TTS synthesizer instance.
#             tts_queue: The queue containing text to be processed into speech.
#             interruptible: Whether the speech playback can be interrupted by new input.
#             speaking_lock: threading.Event, locked while speaking
#         """
#         self._tts = tts
#         self._tts_queue = tts_queue
#         self.interruptible = interruptible # deprecated
#         self._stop_event = threading.Event()
#         self._thread = threading.Thread(target=self._process_queue, daemon=True)
#         self.event_system = EventSystem()
#         self._speaking_lock = speaking_lock
#         self.stt_enabled = True
#
#     def start(self):
#         """
#         Start the speech processing thread.
#         """
#         logger.info("Starting SpeechModule...")
#         self._stop_event.clear()
#         self._thread.start()
#         self._say("speech module activated")
#
#     def stop(self):
#         """
#         Stop the speech processing thread.
#         """
#         logger.info("Stopping SpeechModule...")
#         self._stop_event.set()
#         self._thread.join()
#
#     def _process_queue(self):
#         """
#         Continuously process text from the TTS queue and play audio.
#         """
#         while not self._stop_event.is_set():
#             try:
#                 # Wait for text from the queue with a timeout
#                 generated_text = self._tts_queue.get(timeout=0.1)
#
#                 if generated_text == "<EOS>":  # End-of-stream signal
#                     logger.info("Received end-of-stream signal.")
#                     if queue.Empty:
#                         logger.info("Sending event to listen for response")
#                         self.event_system.publish(EventMessage(
#                             "system",
#                             "listen_for_response",
#                             {}
#                         ))
#                     continue
#
#                 if not generated_text:
#                     logger.warning("Empty string received for TTS processing.")
#                     continue
#
#                 logger.debug(f"Processing TTS for text: {generated_text}")
#                 processed_text = self._process_text(generated_text)
#                 self._say(processed_text)
#
#
#             except queue.Empty:
#                 continue
#             except Exception as e:
#                 logger.error(f"Error in SpeechModule: {e}")
#
#     def _say(self, text: str):
#         """
#         Generate and play TTS audio from text.
#         """
#         logger.info(f"Generating TTS for: {text}")
#         audio = self._tts.generate_speech_audio(text)
#         self._play_audio(audio)
#
#
#     def _play_audio(self, audio):
#         """
#         Play the generated TTS audio.
#         """
#         import sounddevice as sd
#         sd.play(audio, self._tts.rate)
#         sd.wait()  # Wait for the audio to finish playing before continuing
#
#     def _process_text(self, text: str) -> str:
#         """
#         Process text for better pronunciation and cleaner output for TTS.
#
#         Steps:
#         - Replace numbers with words.
#         - Filter out special tags or metadata.
#         - Normalize spacing and punctuation.
#
#         Args:
#             text (str): The text to process.
#
#         Returns:
#             str: The cleaned and processed text.
#         """
#         logger.debug(f"Original TTS text: {text}")
#
#         # Replace numbers with words
#         text = self._replace_numbers_with_words(text)
#
#         # Filter out special tags
#         text = self._filter_special_tags(text)
#
#         # Normalize spacing and punctuation
#         text = self._normalize_text(text)
#
#         logger.debug(f"Processed TTS text: {text}")
#         return text
#
#     def _replace_numbers_with_words(self, text: str) -> str:
#         """
#         Replace numerical values in the text with their word equivalents.
#
#         Args:
#             text (str): The input text.
#
#         Returns:
#             str: The text with numbers replaced.
#         """
#         def number_to_words(match):
#             number = int(match.group())
#             return num2words(number)
#
#         return re.sub(r'\b\d+\b', number_to_words, text)
#
#     def _filter_special_tags(self, text: str) -> str:
#         """
#         Filter out special tags or metadata from the text.
#
#         Args:
#             text (str): The input text.
#
#         Returns:
#             str: The filtered text.
#         """
#         # Remove patterns like *_TAG_[...]_ or START_TOKEN_XXX
#         filtered_text = re.sub(
#             r"(_[A-Z_]+_[A-Z_]+(?:\[[^\]]*\])?)|START_TOKEN_[A-Z_]+|END_TOKEN_[A-Z_]+",
#             "",
#             text
#         )
#         return filtered_text.strip()
#
#     def _normalize_text(self, text: str) -> str:
#         """
#         Normalize spacing, punctuation, and line breaks for cleaner TTS output.
#
#         Args:
#             text (str): The input text.
#
#         Returns:
#             str: The normalized text.
#         """
#         # fix pronunciation of some words
#         text = re.sub(r"(?i)\bplugins\b", "plug-ins", text)  # Match whole words case-insensitively
#         text = re.sub(r"(?i)\bplugin\b", "plug-in", text)  # Match whole words case-insensitively
#         text = re.sub(r"(?i)\bglados\b", "gladys", text)  # Match 'glados' case-insensitively
#
#
#         # Remove extra whitespace and normalize punctuation
#         text = (
#             text.replace("\n\n", ". ")
#                 .replace("\n", ". ")
#                 .replace("  ", " ")
#                 .replace(":", " ")
#         )
#         text = re.sub(r"\s{2,}", " ", text).strip()  # Collapse extra spaces
#         return text
#
