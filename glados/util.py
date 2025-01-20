import re
from typing import List

from num2words import num2words


def replace_numbers_with_words(text):
    # Define a function to convert a number to its word equivalent
    def number_to_words(match):
        number = int(match.group())
        return num2words(number)

    # Use regex to find all numbers in the string and replace them with their word equivalents
    result = re.sub(r'\b\d+\b', number_to_words, text)
    return result

def clean_up_sentence(current_sentence: List[str]):
    """
    Join text, remove inflections and actions, and send to the TTS queue.

    The LLM like to *whisper* things or (scream) things, and prompting is not a 100% fix.
    We use regular expressions to remove text between ** and () to clean up the text.
    Finally, we remove any non-alphanumeric characters/punctuation and send the text
    to the TTS queue.
    """
    sentence = "".join(current_sentence)
    sentence = re.sub(r"\*.*?\*|\(.*?\)", "", sentence)
    sentence = (
        sentence.replace("\n\n", ". ")
        .replace("\n", ". ")
        .replace("  ", " ")
        .replace(":", " ")
    )
    if sentence:
        self.tts_queue.put(sentence)