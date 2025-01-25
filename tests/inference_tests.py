# Add the parent directory to sys.path
import sys
import time
import unittest
from pathlib import Path

from loguru import logger

from glados.system.event_system import EventSystem
from glados.system.plugin import load_plugins, PluginSystem

parent_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(parent_dir))

plugin_manager = PluginSystem()
load_plugins(f"{parent_dir}/plugins")

from trash.llmclient import LLMClient
from glados.config import GladosConfig


def wait_for_message(llm_client, expected_role, expected_content_substring, timeout=5):
    """
    Wait for a specific message to appear in the LLM messages list.

    Args:
        llm_client (LLMClient): The LLM client instance.
        expected_role (str): The expected role of the message ("tool", "assistant", etc.).
        expected_content_substring (str): Substring to search for in the message content.
        timeout (int): Maximum time (in seconds) to wait for the message.

    Returns:
        dict: The matching message if found, or None if timeout occurs.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        messages = llm_client.messages  # Access the messages list
        for message in messages:
            if message.get("role") == expected_role and expected_content_substring in message.get("content", ""):
                logger.info(f"Found expected message: {message}")
                return message
        time.sleep(0.1)  # Avoid tight loop, poll every 100ms
    logger.warning(
        f"Expected message with role '{expected_role}' and content containing '{expected_content_substring}' not found.")
    return None


class TestTimers(unittest.TestCase):
    event_system = EventSystem()
    config = GladosConfig.from_yaml("../glados_config.yml")
    llm_client = LLMClient(
        url=config.completion_url,
        model=config.model,
        headers={"Authorization": f"Bearer {config.api_key}"},
        config=config
    )

    def setUp(self):
        # Ensure LLM is ready by sending an initial "Hi" message
        logger.info("Ensuring LLM is ready...")
        response = self.llm_client.chat("Hi")
        logger.info(f"LLM Response: {response}")
        self.assertGreater(len(response.strip()), 0, "Failed to initialize LLM interaction.")

    def test_timer_expiration(self):
        # Set a timer using the LLM
        logger.info("Setting a timer...")
        self.llm_client.chat("Set a timer for 2 seconds called 'coffee timer'")

        # Wait for the timer setup confirmation
        setup_message = wait_for_message(
            self.llm_client,
            expected_role="assistant",
            expected_content_substring="Timer \'coffee timer\' set for 2 seconds",
            timeout=5
        )
        self.assertIsNotNone(setup_message, "Failed to confirm timer setup.")

        # Wait for the expiration message
        expiration_message = wait_for_message(
            self.llm_client,
            expected_role="tool",
            expected_content_substring="A timer has expired: coffee timer",
            timeout=5
        )
        self.assertIsNotNone(expiration_message, "Expected timer expiration message was not found.")

        # Optionally, verify final assistant response
        assistant_response = wait_for_message(
            self.llm_client,
            expected_role="tool",
            expected_content_substring="A timer has expired",
            timeout=5
        )
        self.assertIsNotNone(assistant_response, "Expected assistant response about timer expiration was not found.")


if __name__ == "__main__":
    unittest.main()
