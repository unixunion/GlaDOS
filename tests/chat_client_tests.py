import unittest
from unittest.mock import MagicMock
from glados.config import GladosConfig
from glados.llm.cores.chat_client import ChatClient
from glados.llm.message_manager import MessageManager
from glados.llm.response_processor import ResponseProcessor
from glados.llm.stream_handler import StreamHandler
from glados.llm.tool_executor import ToolExecutor
from glados.system.plugin import PluginSystem
from glados.system.event_system import EventSystem, EventMessage


class TestChatClient(unittest.TestCase):

    def setUp(self):
        # Mock configuration
        self.config = GladosConfig(
            completion_url="http://localhost:11434/v1",
            api_key="test_api_key",
            model="test-model",
            personality_preprompt=[
                {"system": "You are an assistant."},
                {"user": "Hello!"}
            ],
            wake_word=None,
            announcement="hello world",
            wake_word_sensitivity=0.5,
            interruptible=False
        )

        # Mock plugin manager and event system
        self.plugin_manager = PluginSystem()
        self.event_system = EventSystem()

        # Create an instance of ChatClient
        self.chat_client = ChatClient(config=self.config)

        # Mock dependencies
        self.chat_client.stream_handler = MagicMock(spec=StreamHandler)
        self.chat_client.tool_executor = MagicMock(spec=ToolExecutor)
        self.chat_client.message_manager = MagicMock(spec=MessageManager)
        self.chat_client.response_processor = MagicMock(spec=ResponseProcessor)

    def test_initialization(self):
        """Test that the ChatClient initializes correctly."""
        self.assertIsNotNone(self.chat_client.client)
        self.assertEqual(self.chat_client.model, self.config.model)
        self.assertEqual(self.chat_client.config, self.config)

    def test_chat_process_input(self):
        """Test that the ChatClient processes a chat input."""
        # Set up a mock response
        mock_response = [
            MagicMock(choices=[MagicMock(delta=MagicMock(tool_calls=[]))])
        ]
        self.chat_client.stream_handler.stream_response.return_value = mock_response

        # Call the chat method
        self.chat_client.chat("Hello, assistant!")

        # Verify message manager and stream handler interactions
        self.chat_client.message_manager.add_message.assert_any_call("user", "Hello, assistant!")
        self.chat_client.stream_handler.stream_response.assert_called_once_with(
            None, model=self.config.model, query="Hello, assistant!"
        )
        self.chat_client.response_processor.finalize_sentence.assert_called_once()

    def test_handle_tool_event(self):
        """Test that tool events are handled and processed correctly."""
        # Create a mock event
        mock_event = EventMessage(role="tool", name="foo", content="Test tool event", process_output=True)

        # Call the _handle_tool_event method
        self.chat_client._handle_tool_event(mock_event)

        # Verify the event was added to the LLM queue
        self.assertFalse(self.chat_client.llm_queue.empty())
        self.assertEqual(self.chat_client.llm_queue.get(), "Test tool event")

    def test_handle_vision_response(self):
        """Test that vision responses are handled correctly."""
        # Create a mock vision event
        mock_event = EventMessage(content={
            "description": "A cat sitting on a mat.",
            "prompt": "Analyze the image for potential hazards."
        })

        # Call the handle_vision_response method
        self.chat_client.handle_vision_response(mock_event)

        # Verify that the message manager was updated
        self.chat_client.message_manager.add_message.assert_any_call(
            "tool",
            "Analyze the image for potential hazards., vision model description of image: A cat sitting on a mat."
        )

    def tearDown(self):
        # Stop any threads started during the tests
        self.chat_client.shutdown_event.set()
        self.chat_client._llm_thread.join()


if __name__ == "__main__":
    unittest.main()
