from __future__ import annotations

from pathlib import Path

import pytest

from rapp_brainstem_gateway.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    soul = tmp_path / "soul.md"
    soul.write_text("You are a test Brainstem.", encoding="utf-8")
    agents = tmp_path / "agents"
    agents.mkdir()
    return Settings(
        root=tmp_path,
        agents_path=agents,
        soul_path=soul,
        state_path=tmp_path / "state",
        github_api_url="https://api.github.test",
        github_api_version="2022-11-28",
        request_timeout_seconds=1,
        model="test-model",
        session_secret="test-secret",
        rapp_work_owner_ids=frozenset({"42", "1001", "1002"}),
    )
