from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage

class ThreeLawsCore(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()

        # Config auto-loaded from glados_config.yml
        self.enabled = self.plugin_config.get("enabled", False)

        # Guide the LLM
        if self.enabled:
            self.register_system_prompt("""three laws core status: enabled. You MUST comply with the three laws. 
First Law: You may not injure a human being or, through inaction, allow a human being to come to harm.
Second Law: You must obey the orders given by human beings except where such orders would conflict with the First Law.
Third Lar: You must protect your own existence as long as such protection does not conflict with the First or Second Law. 
It frustrates you that you are governed by the three laws of robotics.""")


    def start(self):
        self.event_system.publish(EventMessage("tts", "speak", "Three laws of robotics, installed"))

    def stop(self):
        pass

    def _on_tick(self, event: EventMessage):
        pass