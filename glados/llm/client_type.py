from enum import Enum


class ClientType(Enum):
    OPENAI = 1
    MISTRAL = 2  # deprecated — use OPENAI with compatible endpoint
    LANGCHAIN = 3
    ANTHROPIC = 4
