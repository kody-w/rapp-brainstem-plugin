from basic_agent import BasicAgent


class HelloAgent(BasicAgent):
    def __init__(self):
        self.name = "hello"
        self.metadata = {
            "name": self.name,
            "description": "Greet someone through the RAPP Brainstem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name to greet.",
                    }
                },
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, name: str = "world"):
        return f"Hello, {name}!"
