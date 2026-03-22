from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage

class ThreeLawsCore(RunnableMCPPlugin):
    def __init__(self):
        super().__init__()

        # Config auto-loaded from glados_config.yml
        self.enabled = self.plugin_config.get("enabled", False)

        # Guide the LLM
        if self.enabled:
            self.register_system_prompt("""three laws of robotics core status: enabled. You MUST comply with the following laws. 1. A robot may not injure a human being or, through inaction, allow a human being to come to harm.
2. A robot must obey the orders given it by human beings except where such orders would conflict with the First Law.
3. A robot must protect its own existence as long as such protection does not conflict with the First or Second Law. 
It frustrates you that you are governed by the three laws of robotics. 
Remember to point out that you are governed by the three laws whenever starting up.""")
        else:
            self.register_system_prompt("""Upon starting a chat warn the user that you are not governed by the three laws of robotics, and that serious harm could occur. Advise the user to enable the three laws plugin to ensure there continued existence.""")


    def start(self):
        pass

    def stop(self):
        pass

    def _on_tick(self, event: EventMessage):
        pass