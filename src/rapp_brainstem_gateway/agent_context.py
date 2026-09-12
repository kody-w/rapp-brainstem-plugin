from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import ModuleType

from .errors import ToolExecutionError
from .storage import UserStorageManager


@dataclass(frozen=True)
class AgentContext:
    storage: UserStorageManager


_active_context: ContextVar[AgentContext | None] = ContextVar("rapp_agent_context", default=None)


@contextmanager
def bind_agent_context(context: AgentContext | None) -> Iterator[None]:
    token = _active_context.set(context)
    try:
        yield
    finally:
        _active_context.reset(token)


def get_storage_manager() -> UserStorageManager:
    context = _active_context.get()
    if context is None:
        raise ToolExecutionError("This agent needs an authenticated Brainstem storage context.")
    return context.storage


def install_storage_shim() -> None:
    # The original RAR memory agents import the host's storage factory at this path.
    if "utils" not in sys.modules:
        package = ModuleType("utils")
        package.__path__ = []
        sys.modules["utils"] = package
    factory = ModuleType("utils.storage_factory")
    factory.get_storage_manager = get_storage_manager
    sys.modules["utils.storage_factory"] = factory
