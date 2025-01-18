import re

from num2words import num2words


def replace_numbers_with_words(text):
    # Define a function to convert a number to its word equivalent
    def number_to_words(match):
        number = int(match.group())
        return num2words(number)

    # Use regex to find all numbers in the string and replace them with their word equivalents
    result = re.sub(r'\b\d+\b', number_to_words, text)
    return result