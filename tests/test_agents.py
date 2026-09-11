from __future__ import annotations

from rapp_brainstem_gateway.agents import AgentRegistry


def test_loads_canonical_rapp_agent_import(settings):
    agent_file = settings.agents_path / "canonical_agent.py"
    agent_file.write_text(
        """
from agents.basic_agent import BasicAgent

class CanonicalAgent(BasicAgent):
    def __init__(self):
        self.name = "canonical"
        self.metadata = {
            "name": self.name,
            "description": "Canonical test agent.",
            "parameters": {"type": "object", "properties": {}},
        }
        super().__init__(self.name, self.metadata)

    def perform(self, **kwargs):
        return "ok"
""",
        encoding="utf-8",
    )

    loaded = AgentRegistry(settings.agents_path).load()

    assert len(loaded) == 1
    assert loaded[0].name == "canonical"
    assert loaded[0].instance.perform() == "ok"
