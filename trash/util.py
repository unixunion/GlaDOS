import base64
import re
from typing import List

from loguru import logger
from num2words import num2words


def cleanup_sentence(text):
    """
    Cleans up the input sentence by performing the following:
    - Replace the words that glados cant pronounce to phonetic equivilants
    - Replace all numeric values with their word equivalents.
    """
    logger.info(f"Fixing text: {text}")
    # Fix spacing around punctuation
    # text = re.sub(r"\s+([.,!?;:])", r"\1", text)  # Remove space before punctuation
    # text = re.sub(r"([a-zA-Z])\s+'([a-zA-Z])", r"\1'\2", text)  # Fix contractions
    # text = re.sub(r"\s+", " ", text).strip()  # Normalize extra spaces

    # Replace 'plugins' with 'plug-ins' (case-insensitive)
    # text = re.sub(r"plugins", "plug-ins", text, flags=re.IGNORECASE)

    # Replace 'glados' with 'glad-os' (case-insensitive)
    # text = re.sub(r"glados", "glad-os", text, flags=re.IGNORECASE)

    text = re.sub(r"(?i)\bplugins\b", "plug-ins", text)  # Match whole words case-insensitively
    text = re.sub(r"(?i)\bplugin\b", "plug-in", text)  # Match whole words case-insensitively
    text = re.sub(r"(?i)\bglados\b", "glad-os", text)  # Match 'glados' case-insensitively

    # Call replace_numbers_with_words to replace numbers with words
    text = replace_numbers_with_words(text)

    logger.info(f"fixed text: {text}")

    return text


def replace_numbers_with_words(text):
    # Define a function to convert a number to its word equivalent
    def number_to_words(match):
        number = int(match.group())
        return num2words(number)

    # Use regex to find all numbers in the string and replace them with their word equivalents
    result = re.sub(r'\b\d+\b', number_to_words, text)
    return result


# def clean_up_sentence(current_sentence: List[str]):
#     """
#     Join text, remove inflections and actions, and send to the TTS queue.
#
#     The LLM like to *whisper* things or (scream) things, and prompting is not a 100% fix.
#     We use regular expressions to remove text between ** and () to clean up the text.
#     Finally, we remove any non-alphanumeric characters/punctuation and send the text
#     to the TTS queue.
#     """
#     sentence = "".join(current_sentence)
#     sentence = re.sub(r"\*.*?\*|\(.*?\)", "", sentence)
#     sentence = (
#         sentence.replace("\n\n", ". ")
#         .replace("\n", ". ")
#         .replace("  ", " ")
#         .replace(":", " ")
#     )
#     if sentence:
#         self.tts_queue.put(sentence)

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
