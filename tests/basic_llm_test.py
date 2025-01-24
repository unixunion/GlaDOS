import time
import unittest
from pathlib import Path

from glados import tts
from glados.config import GladosConfig
from glados.llm.client import Client
from glados.llm.voice_cores.glados_speech_module import GladosSpeechModule
from plugins.context_manager import ContextManager
from plugins.event_system.event_system import EventSystem
from plugins.plugin_system.plugin_manager import PluginManager, load_plugins

plugin_manager = PluginManager()
load_plugins("../plugins")
context_manager = ContextManager()


class BasicLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.event_system = EventSystem()
        cls.config = GladosConfig.from_yaml("../glados_config.yml")
        cls.client = Client(cls.config)

        cls.tts_synthesizer = tts.Synthesizer(
            model_path=str(Path("../models") / cls.config.voice_model),
            speaker_id=cls.config.speaker_id,
        )

        cls.speech_module = GladosSpeechModule(
            tts=cls.tts_synthesizer,
            tts_queue=cls.client.tts_queue,
            interruptible=False
        )

    def test_vision(self):
        # Start the SpeechModule in the background
        self.speech_module.start()

        # Send a message to the client
        self.client.chat(
            "Hello GlaDOS, how are you today you potatoe dweller",
            tools=plugin_manager.get_available_tools()
        )

        # Wait for the SpeechModule to process the response
        for _ in range(100):  # Wait up to 10 seconds
            if self.client.is_tts_queue_empty():
                break
            time.sleep(0.1)

        # Shutdown the SpeechModule
        self.speech_module.stop()


if __name__ == "__main__":
    unittest.main()
