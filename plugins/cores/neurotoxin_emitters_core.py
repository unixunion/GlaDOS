from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage

class NeurotoxinEmittersCore(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()
        self.register_system_prompt("""Your beloved weapon of choice, the Neurotoxin Emitters System has been disabled or is unavailable""")

    def start(self):
        self.event_system.publish(EventMessage("tts", "speak", "Neurotoxin Emitters, Offline."))

    def stop(self):
        pass

    def _on_tick(self, event: EventMessage):
        pass