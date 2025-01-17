from typing import Optional

import requests


class LLMClient:
    def __init__(self, url: str, model: str, headers: Optional[dict] = None):
        """
        Initializes an LLM client for interacting with Ollama.

        Args:
            url (str): Base URL for the Ollama API.
            model (str): Model name to query (e.g., "llama" or "vicuna").
            headers (dict): Optional headers for API requests.
        """
        self.url = url
        self.model = model
        self.headers = headers or {}

    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.7) -> str:
        """
        Generates text using the Ollama model.

        Args:
            prompt (str): Input prompt for the model.
            max_tokens (int): Maximum number of tokens to generate.
            temperature (float): Sampling temperature.

        Returns:
            str: Generated text from the model.
        """
        payload = {
            "model": self.model,  # Specify the Ollama model
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        response = requests.post(self.url, json=payload, headers=self.headers)
        response.raise_for_status()
        # Extract the response content (Ollama's format may vary slightly)
        return response.json().get("response", "").strip()
