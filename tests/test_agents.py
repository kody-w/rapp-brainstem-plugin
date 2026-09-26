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


PROBE_AGENT = """
from agents.basic_agent import BasicAgent

class ProbeAgent(BasicAgent):
    def __init__(self):
        self.name = {name!r}
        self.metadata = {{
            "name": self.name,
            "description": "Discovery probe.",
            "parameters": {{"type": "object", "properties": {{}}}},
        }}
        super().__init__(self.name, self.metadata)

    def perform(self, **kwargs):
        return self.name
"""


def test_loads_only_top_level_agents(settings):
    """Only top-level *_agent.py files are live; every subfolder is parked.

    RAPP proposal 0001 (kody-w/RAPP#124): a nested experimental_agents/x_agent.py is
    NOT loaded, and a top-level experimental_thing_agent.py IS loaded.
    """
    layout = {
        "top_agent.py": "top",
        "experimental_thing_agent.py": "experimental_thing",
        "experimental_agents/x_agent.py": "nested_experimental_x",
        "disabled_agents/y_agent.py": "nested_disabled_y",
        "parked/z_agent.py": "nested_parked_z",
    }
    for relative, name in layout.items():
        path = settings.agents_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PROBE_AGENT.format(name=name), encoding="utf-8")

    loaded = AgentRegistry(settings.agents_path).load()

    assert sorted(agent.name for agent in loaded) == ["experimental_thing", "top"]
