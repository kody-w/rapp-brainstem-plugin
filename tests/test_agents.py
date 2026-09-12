from __future__ import annotations

import os
from threading import Event

import pytest

from agents.basic_agent import BasicAgent
from rapp_brainstem_gateway.agents import AgentRegistry, LoadedAgent


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
    assert type(loaded[0].instance).__bases__ == (BasicAgent,)


async def test_flat_base_import_and_same_size_source_hot_reload(settings):
    path = settings.agents_path / "flat_agent.py"
    source = """
from basic_agent import BasicAgent
from dataclasses import dataclass

@dataclass
class Value:
    text: str

class FlatAgent(BasicAgent):
    def __init__(self):
        super().__init__("flat", {
            "name": "flat", "parameters": {"type": "object", "properties": {}}
        })
    def perform(self, **kwargs):
        return Value("first").text
"""
    path.write_text(source, encoding="utf-8")
    stat = path.stat()
    registry = AgentRegistry(settings.agents_path)
    assert await registry.load()[0].invoke({}) == "first"
    path.write_text(source.replace("first", "other"), encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert await registry.load()[0].invoke({}) == "other"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_sync_and_async_perform_are_supported(settings, asynchronous):
    declaration = "async def" if asynchronous else "def"
    (settings.agents_path / "mode_agent.py").write_text(
        f"""
from basic_agent import BasicAgent
class ModeAgent(BasicAgent):
    def __init__(self):
        super().__init__("mode", {{"name": "mode", "parameters": {{"type": "object"}}}})
    {declaration} perform(self, **kwargs):
        return "done"
""",
        encoding="utf-8",
    )
    assert await AgentRegistry(settings.agents_path).load()[0].invoke({}) == "done"


async def test_synchronous_agent_does_not_block_the_event_loop():
    import asyncio

    released = Event()

    class BlockingAgent(BasicAgent):
        def __init__(self):
            super().__init__("blocking", {})

        def perform(self, **kwargs):
            return released.wait(timeout=1)

    async def release_from_event_loop():
        await asyncio.sleep(0)
        released.set()

    agent = LoadedAgent("blocking", "", {"type": "object"}, BlockingAgent())
    result, _ = await asyncio.gather(agent.invoke({}), release_from_event_loop())
    assert result is True
