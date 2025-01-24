import json

from glados.llm.message_manager import MessageManager, Context


class EventHandler:
    def __init__(self, message_manager: MessageManager = None):
        self.message_manager = message_manager

    def handle_event(self, event, context: Context = Context.GENERAL):
        if event.get("name") == "tick":
            return
        self.message_manager.add_message("tool", json.dumps(event), name=event.get("name"), context=context)
