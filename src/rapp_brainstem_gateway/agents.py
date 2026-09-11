from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


class BasicAgent:
    def __init__(self, name: str | None = None, metadata: dict[str, Any] | None = None) -> None:
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

    def perform(self, **kwargs: Any) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class LoadedAgent:
    name: str
    description: str
    parameters: dict[str, Any]
    instance: BasicAgent


class AgentRegistry:
    def __init__(self, agents_path: Path) -> None:
        self._agents_path = agents_path

    def load(self) -> list[LoadedAgent]:
        agents: list[LoadedAgent] = []
        if not self._agents_path.exists():
            return agents

        shim = ModuleType("basic_agent")
        shim.BasicAgent = BasicAgent
        sys.modules["basic_agent"] = shim
        agents_package = sys.modules.get("agents")
        if agents_package is None:
            agents_package = ModuleType("agents")
            agents_package.__path__ = []
            sys.modules["agents"] = agents_package
        sys.modules["agents.basic_agent"] = shim

        for path in sorted(self._agents_path.rglob("*_agent.py")):
            if path.name == "basic_agent.py" or path.name.startswith("_"):
                continue
            module = self._load_module(path)
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is BasicAgent or not issubclass(candidate, BasicAgent):
                    continue
                if candidate.__module__ != module.__name__:
                    continue
                instance = candidate()
                metadata = instance.metadata
                name = str(metadata.get("name") or instance.name)
                parameters = metadata.get("parameters") or {
                    "type": "object",
                    "properties": {},
                }
                agents.append(
                    LoadedAgent(
                        name=name,
                        description=str(metadata.get("description", "")),
                        parameters=parameters,
                        instance=instance,
                    )
                )
        return agents

    @staticmethod
    def _load_module(path: Path) -> ModuleType:
        digest = abs(hash(path.resolve()))
        module_name = f"rapp_dynamic_agent_{digest}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load agent module: {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
