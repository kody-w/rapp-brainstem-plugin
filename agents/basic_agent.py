class BasicAgent:
    """Base class for portable RAPP single-file agents."""

    def __init__(self, name=None, metadata=None):
        self.name = name or getattr(self, "name", self.__class__.__name__)
        self.metadata = metadata or getattr(
            self,
            "metadata",
            {
                "name": self.name,
                "description": "RAPP agent",
                "parameters": {"type": "object", "properties": {}},
            },
        )

    def perform(self, **kwargs):
        raise NotImplementedError
