from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import inspect
import json
import logging
import re
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from jsonschema import Draft202012Validator, ValidationError

from agents import basic_agent
from agents.basic_agent import BasicAgent

from .agent_context import AgentContext, bind_agent_context, install_storage_shim
from .errors import ToolExecutionError
from .rar import DEFAULT_AGENTS_PATH, verify_installed_source
from .storage import UserStorageManager

logger = logging.getLogger(__name__)
sys.modules["basic_agent"] = basic_agent
install_storage_shim()


class Agent(Protocol):
    name: str
    metadata: dict[str, Any]

    def perform(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class LoadedAgent:
    name: str
    description: str
    parameters: dict[str, Any]
    instance: Agent
    context: AgentContext | None = None

    async def invoke(self, arguments: dict[str, Any]) -> Any:
        try:
            Draft202012Validator(self.parameters).validate(arguments)
        except ValidationError as exc:
            raise ToolExecutionError(f"Invalid arguments for {self.name}: {exc.message}") from exc
        with bind_agent_context(self.context):
            if inspect.iscoroutinefunction(self.instance.perform):
                value = await self.instance.perform(**arguments)
            else:
                value = await asyncio.to_thread(self._perform, arguments)
                if inspect.isawaitable(value):
                    value = await value
        parsed = value
        if isinstance(value, str):
            if value.lower().startswith("error:"):
                raise ToolExecutionError(value)
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict) and parsed.get("status") in {"error", "failed", "failure"}:
            raise ToolExecutionError(str(parsed.get("message") or parsed.get("error") or parsed))
        return value

    def _perform(self, arguments: dict[str, Any]) -> Any:
        storage = getattr(self.instance, "storage_manager", None)
        transaction = (
            storage.transaction() if isinstance(storage, UserStorageManager) else nullcontext()
        )
        # The legacy memory agents read then write; keep the whole operation atomic.
        with transaction:
            return self.instance.perform(**arguments)


@dataclass(frozen=True)
class AgentCatalog:
    agents: list[LoadedAgent]
    errors: list[str]


class AgentRegistry:
    def __init__(
        self,
        agents_path: Path,
        *,
        state_path: Path | None = None,
        include_defaults: bool = False,
    ) -> None:
        self._agents_path = agents_path
        self._state_path = state_path
        self._include_defaults = include_defaults

    def load(self, *, user_id: str | None = None) -> list[LoadedAgent]:
        catalog = self.catalog(user_id=user_id)
        if catalog.errors:
            raise ToolExecutionError("; ".join(catalog.errors))
        return catalog.agents

    def catalog(self, *, user_id: str | None = None) -> AgentCatalog:
        context = None
        if user_id is not None and self._state_path is not None:
            context = AgentContext(UserStorageManager(self._state_path, user_id))
        roots = [DEFAULT_AGENTS_PATH] if self._include_defaults else []
        if self._agents_path.resolve() not in {path.resolve() for path in roots}:
            roots.append(self._agents_path)
        paths = [
            (path, path.name, False)
            for root in roots
            for path in sorted(root.rglob("*_agent.py"))
            if path.name != "basic_agent.py"
            and not any(part.startswith((".", "_")) for part in path.relative_to(root).parts)
        ]
        if context is not None:
            install_path = context.storage.user_path / "agents"
            if install_path.exists():
                paths.extend(
                    (path, f"RAR package {path.name}", True)
                    for path in sorted(install_path.iterdir())
                    if path.is_dir() and not path.name.startswith(".")
                )
        loaded: list[LoadedAgent] = []
        errors: list[str] = []
        names: set[str] = set()
        with bind_agent_context(context):
            for path, label, installed in paths:
                try:
                    if installed:
                        path, receipt = verify_installed_source(path)
                        label = receipt["name"]
                    candidates = self._load_file(path, context)
                    candidate_names = [agent.name for agent in candidates]
                    if len(set(candidate_names)) != len(candidate_names) or names.intersection(
                        candidate_names
                    ):
                        raise ToolExecutionError(
                            "Duplicate tool names cannot replace another agent."
                        )
                    loaded.extend(candidates)
                    names.update(candidate_names)
                except Exception as exc:
                    logger.exception("Unable to load Brainstem agent %s", label)
                    if isinstance(exc, ModuleNotFoundError):
                        detail = f"missing Python dependency {exc.name!r}"
                    elif isinstance(exc, ToolExecutionError):
                        detail = str(exc)
                    else:
                        detail = f"{type(exc).__name__}; see gateway logs"
                    errors.append(f"{label}: {detail}")
        return AgentCatalog(loaded, errors)

    @classmethod
    def _load_file(cls, path: Path, context: AgentContext | None) -> list[LoadedAgent]:
        result: list[LoadedAgent] = []
        module = cls._load_module(path, context)
        try:
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is BasicAgent or not issubclass(candidate, BasicAgent):
                    continue
                if candidate.__module__ != module.__name__:
                    continue
                if not callable(getattr(candidate, "perform", None)):
                    raise ToolExecutionError("RAPP agents must implement perform().")
                instance = candidate()
                metadata = instance.metadata
                name = metadata.get("name") or instance.name
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                    raise ToolExecutionError(
                        "Agent tool names must use 1-64 letters, digits, _ or -."
                    )
                parameters = metadata.get("parameters") or {
                    "type": "object",
                    "properties": {},
                }
                parameters = copy.deepcopy(parameters)
                if "user_guid" in parameters.get("properties", {}):
                    del parameters["properties"]["user_guid"]
                    parameters["required"] = [
                        name for name in parameters.get("required", []) if name != "user_guid"
                    ]
                Draft202012Validator.check_schema(parameters)
                result.append(
                    LoadedAgent(
                        name=name,
                        description=str(metadata.get("description", "")),
                        parameters=parameters,
                        instance=instance,
                        context=context,
                    )
                )
        finally:
            sys.modules.pop(module.__name__, None)
        if not result:
            raise ToolExecutionError("No callable BasicAgent subclass was found.")
        return result

    @staticmethod
    def _load_module(path: Path, context: AgentContext | None = None) -> ModuleType:
        user_id = context.storage.current_guid if context is not None else "host"
        digest = hashlib.sha256(f"{user_id}:{path.resolve()}".encode()).hexdigest()
        module_name = f"rapp_dynamic_agent_{digest}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ToolExecutionError(f"Unable to load agent module: {path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            # Always compile admitted source, not a possibly stale timestamp-based .pyc.
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        return module
