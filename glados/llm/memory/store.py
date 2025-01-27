from langgraph.store.memory import InMemoryStore

class MemoryStore:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = MemoryStore()
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.store = InMemoryStore()
        self._initialized = True

    def get_store(self):
        return self.store
