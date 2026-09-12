from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .errors import ToolExecutionError


class UserStorageManager:
    """The RAPP storage contract, permanently bound to an authenticated GitHub user."""

    def __init__(self, state_path: Path, user_id: str) -> None:
        if not re.fullmatch(r"[0-9]+", user_id):
            raise ToolExecutionError("Agent storage requires an authenticated GitHub user ID.")
        self.current_guid = user_id
        self.user_path = state_path / user_id
        self._connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"brainstem_storage_{user_id}", default=None
        )

    def set_memory_context(self, user_guid: str | None = None) -> None:
        if user_guid is not None and user_guid != self.current_guid:
            raise ToolExecutionError(
                "Memory belongs to the authenticated user; switching is denied."
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._connection.get() is not None:
            yield
            return
        self.user_path.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.user_path / "agent-storage.sqlite3", timeout=5)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents "
                "(area TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL, "
                "PRIMARY KEY (area, name))"
            )
            connection.execute("BEGIN IMMEDIATE")
            token = self._connection.set(connection)
            try:
                yield
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                self._connection.reset(token)
        finally:
            connection.close()

    def read_json(self) -> dict[str, Any]:
        content = self.read_file("memory", "memories.json")
        if content is None:
            return {}
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ToolExecutionError("Stored memory is invalid; it has not been overwritten.")
        return value

    def write_json(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise ToolExecutionError("Memory must be a JSON object.")
        self.write_file("memory", "memories.json", json.dumps(value))

    def read_file(self, area: str, name: str) -> str | None:
        with self.transaction():
            connection = self._connection.get()
            assert connection is not None
            row = connection.execute(
                "SELECT content FROM documents WHERE area = ? AND name = ?", (area, name)
            ).fetchone()
        return row[0] if row is not None else None

    def write_file(self, area: str, name: str, content: str) -> None:
        with self.transaction():
            connection = self._connection.get()
            assert connection is not None
            connection.execute(
                "INSERT INTO documents (area, name, content) VALUES (?, ?, ?) "
                "ON CONFLICT(area, name) DO UPDATE SET content = excluded.content",
                (area, name, content),
            )
