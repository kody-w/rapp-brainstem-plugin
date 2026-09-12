from __future__ import annotations

import asyncio
import json

import pytest

from agents.basic_agent import BasicAgent
from rapp_brainstem_gateway.agents import AgentRegistry
from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemService
from rapp_brainstem_gateway.errors import ToolExecutionError
from rapp_brainstem_gateway.storage import UserStorageManager
from scripts.sync_rar_agents import sync

DEFAULT_TOOLS = {"ContextMemory", "ManageMemory", "HackerNews", "RARRemoteAgent"}


def load_defaults(settings, user_id="42"):
    registry = AgentRegistry(
        settings.agents_path, state_path=settings.state_path, include_defaults=True
    )
    return {agent.name: agent for agent in registry.load(user_id=user_id)}


def test_defaults_are_verbatim_locked_rar_sources():
    sync(check=True)


def test_fresh_brainstem_exposes_defaults_and_one_real_base_class(settings):
    loaded = load_defaults(settings)
    assert set(loaded) == DEFAULT_TOOLS
    assert all(type(agent.instance).__bases__ == (BasicAgent,) for agent in loaded.values())
    assert all("user_guid" not in agent.parameters["properties"] for agent in loaded.values())
    status = BrainstemService(settings).status(GitHubIdentity("42", "octocat"))
    assert status["loadedAgentCount"] == len(DEFAULT_TOOLS)
    assert {agent["name"] for agent in status["agents"]} == DEFAULT_TOOLS
    assert status["agentErrors"] == []


async def test_memory_survives_reload_and_is_private_to_user(settings):
    original = load_defaults(settings)
    saved = await original["ManageMemory"].invoke(
        {"memory_type": "preference", "content": "Use concise project summaries."}
    )
    assert "Successfully stored" in saved

    reloaded = load_defaults(settings)
    recalled = await reloaded["ContextMemory"].invoke({})
    assert "Use concise project summaries." in recalled
    another_user = await load_defaults(settings, "84")["ContextMemory"].invoke({})
    assert "Use concise project summaries." not in another_user
    assert "don't have any memories" in another_user


async def test_memory_cannot_switch_to_caller_supplied_identity(settings):
    loaded = load_defaults(settings)
    with pytest.raises(ToolExecutionError, match="switching is denied"):
        await loaded["ManageMemory"].invoke(
            {"memory_type": "fact", "content": "Not someone else's memory", "user_guid": "84"}
        )
    with pytest.raises(ToolExecutionError, match="switching is denied"):
        await loaded["ContextMemory"].invoke({"user_guid": "84"})
    assert UserStorageManager(settings.state_path, "42").read_json() == {}
    assert UserStorageManager(settings.state_path, "84").read_json() == {}


async def test_concurrent_memory_writes_do_not_lose_updates(settings):
    agents = [load_defaults(settings)["ManageMemory"] for _ in range(16)]
    await asyncio.gather(
        *(
            agent.invoke({"memory_type": "fact", "content": f"Fact {index}"})
            for index, agent in enumerate(agents)
        )
    )
    memories = UserStorageManager(settings.state_path, "42").read_json()
    assert {value["message"] for value in memories.values()} == {
        f"Fact {index}" for index in range(16)
    }


async def test_canonical_memory_error_is_a_tool_failure(settings):
    with pytest.raises(ToolExecutionError, match="No content provided"):
        await load_defaults(settings)["ManageMemory"].invoke({"memory_type": "fact", "content": ""})


def test_storage_rolls_back_failed_operation(settings):
    storage = UserStorageManager(settings.state_path, "42")
    with pytest.raises(RuntimeError, match="interrupted"), storage.transaction():
        storage.write_json({"entry": {"message": "Do not commit this"}})
        raise RuntimeError("interrupted")
    assert storage.read_json() == {}


@pytest.mark.parametrize("user_id", ["../84", "/tmp/84", "octocat", "", "42/../84"])
def test_storage_rejects_non_github_identifiers(settings, user_id):
    with pytest.raises(ToolExecutionError, match="authenticated GitHub user ID"):
        UserStorageManager(settings.state_path, user_id)


async def test_hacker_news_returns_real_story_shape_and_links(settings, monkeypatch):
    agent = load_defaults(settings)["HackerNews"]
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/topstories.json"):
            return [101, 102]
        if url.endswith("/101.json"):
            return {"title": "A linked story", "url": "https://example.test/story", "score": 10}
        return {"title": "Ask HN", "score": 5, "by": "author", "descendants": 3}

    monkeypatch.setitem(agent.instance.perform.__globals__, "_fetch_json", fetch)
    result = json.loads(await agent.invoke({"count": 2}))
    assert result["status"] == "success"
    assert len(result["stories"]) == 2
    assert result["stories"][1]["url"] == "https://news.ycombinator.com/item?id=102"
    assert "[A linked story](https://example.test/story)" in result["summary"]
    assert len(calls) == 3


async def test_hacker_news_failure_is_not_reported_as_success(settings, monkeypatch):
    agent = load_defaults(settings)["HackerNews"]

    def fail(_url):
        raise RuntimeError("HN unavailable")

    monkeypatch.setitem(agent.instance.perform.__globals__, "_fetch_json", fail)
    with pytest.raises(ToolExecutionError, match="HN unavailable"):
        await agent.invoke({"count": 2})
    with pytest.raises(ToolExecutionError, match="Invalid arguments"):
        await agent.invoke({"count": 0})


async def test_rar_tool_cannot_choose_an_output_directory(settings, tmp_path):
    with pytest.raises(ToolExecutionError, match="Additional properties"):
        await load_defaults(settings)["RARRemoteAgent"].invoke(
            {"action": "list_installed", "output_dir": str(tmp_path / "escape")}
        )
    assert not (tmp_path / "escape").exists()
