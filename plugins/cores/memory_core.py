from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin
from glados.system.event_system import EventMessage
from glados.system.intent_classifier import IntentClassifier


# Virtual intent names — not real tools, intercepted pre-LLM by ChatClient
MEMORY_REMEMBER_INTENT = "_memory_remember"
MEMORY_RECALL_INTENT = "_memory_recall"
MEMORY_FORGET_ALL_INTENT = "_memory_forget_all"


class MemoryCore(RunnableMCPPlugin):
    """Plugin that registers memory intents with the IntentClassifier and
    provides a system prompt so the LLM knows it has persistent memory.

    Memory operations are handled pre-LLM by ChatClient — no tool calls needed.
    The IntentClassifier detects "remember"/"recall" intent, ChatClient intercepts
    and handles storage/retrieval directly.
    """

    def __init__(self):
        super().__init__()
        logger.info("Instantiating Memory Core")

        # Register memory intents with the shared IntentClassifier
        classifier = IntentClassifier()
        classifier.add_intent(MEMORY_REMEMBER_INTENT, [
            "remember that",
            "remember this",
            "don't forget",
            "don't forget that",
            "note that",
            "keep in mind",
            "keep in mind that",
            "save that",
            "save to memory",
            "store in memory",
            "please remember",
            "I want you to remember",
            "make a note",
            "remember my preference",
            "remember I like",
            "remember I prefer",
            "remember I am",
            "remember I have",
        ])
        classifier.add_intent(MEMORY_RECALL_INTENT, [
            "do you remember",
            "what did I say about",
            "what did I tell you about",
            "what did I mention about",
            "what do you remember about",
            "what do you know about",
            "recall",
            "can you recall",
            "have we talked about",
            "have we discussed",
            "what are my preferences",
            "what is my preference",
            "what do you remember",
            "do you know my",
            "what's in your memory about",
            "search your memory",
        ])
        classifier.add_intent(MEMORY_FORGET_ALL_INTENT, [
            "forget everything",
            "clear your memory",
            "clear all memories",
            "erase your memory",
            "delete all memories",
            "wipe your memory",
            "reset your memory",
            "forget all",
            "clear memory",
            "erase all memories",
        ])
        classifier.retrain()
        logger.success("Memory intents registered with IntentClassifier")

        # Register with PluginSystem so ChatClient can look up the activity
        from glados.system.plugin import PluginSystem
        plugin_system = PluginSystem()
        for intent_name in [MEMORY_REMEMBER_INTENT, MEMORY_RECALL_INTENT, MEMORY_FORGET_ALL_INTENT]:
            plugin_system.plugins[intent_name] = {
                "function": None,
                "description": "Memory operation (handled pre-LLM)",
                "llm_function_request": {},
                "process_output": True,
                "callable": None,
                "activity": [Activity.GENERAL, Activity.SYSTEM],
            }

        self.register_system_prompt(
            "You have persistent memory that survives across conversations and restarts.\n"
            "Relevant past conversations and stored facts are automatically provided to you as context — "
            "look for [Relevant memories] in your system messages.\n"
            "When the user says 'remember that...' or 'don't forget...', the fact is automatically stored "
            "in your memory — just confirm briefly that you'll remember it.\n"
            "When the user asks 'do you remember...' or 'what did I say about...', relevant memories are "
            "automatically searched and provided — use them to answer the question.\n"
            "Facts marked with [fact] are things the user explicitly asked you to remember — "
            "treat these with higher priority than general conversation history."
        )

    def start(self):
        logger.info("MemoryTools plugin started")
        self.event_system.publish(EventMessage("tts", "speak", "Memory Core, Online."))

    def stop(self):
        logger.info("MemoryTools plugin stopped")
