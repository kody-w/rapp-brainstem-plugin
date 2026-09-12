from __future__ import annotations

import httpx
import pytest

from rapp_brainstem_gateway.agents import AgentRegistry
from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemService
from rapp_brainstem_gateway.errors import ToolExecutionError
from rapp_brainstem_gateway.rar import RAR_RAW_BASE, RARClient, source_hash
from rapp_brainstem_gateway.storage import UserStorageManager

SOURCE = b"""
from agents.basic_agent import BasicAgent

__manifest__ = {
    "schema": "rapp-agent/1.0",
    "name": "@example/example_agent",
    "version": "1.0.0",
    "dependencies": ["@rapp/basic_agent"],
}

class ExampleAgent(BasicAgent):
    def __init__(self):
        super().__init__("Example", {
            "name": "Example",
            "description": "An example RAR package.",
            "parameters": {"type": "object", "properties": {}},
        })

    def perform(self, **kwargs):
        return "example-ready"
"""


@pytest.fixture
def rar(settings):
    entry = {
        "name": "@example/example_agent",
        "display_name": "Example agent",
        "version": "1.0.0",
        "description": "Example package for project summaries.",
        "category": "core",
        "quality_tier": "official",
        "dependencies": ["@rapp/basic_agent"],
        "_sha256": source_hash(SOURCE),
        "_file": "agents/@example/example_agent.py",
    }
    state = {"entry": entry, "source": SOURCE, "requests": [], "status": 200}

    def respond(request):
        state["requests"].append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"])
        if request.url == f"{RAR_RAW_BASE}/registry.json":
            return httpx.Response(200, json={"agents": [state["entry"]]})
        assert request.url == f"{RAR_RAW_BASE}/agents/@example/example_agent.py"
        return httpx.Response(200, content=state["source"])

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = RARClient(UserStorageManager(settings.state_path, "42"), client=http)
        yield client, state


def install(client):
    return client.perform(action="install", agent_name="@example/example_agent", confirm=True)


def test_discovery_search_details_and_cache_use_real_rar_fields(rar, monkeypatch):
    client, state = rar
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated-server-token")
    monkeypatch.setenv("GH_TOKEN", "unrelated-cli-token")
    discovered = client.perform(action="discover")
    searched = client.perform(action="search", query="project summaries", limit=1)
    info = client.perform(action="get_info", agent_name="@example/example_agent")
    assert discovered["total"] == searched["total"] == 1
    assert info["agent"]["sha256"] == source_hash(SOURCE)
    assert info["agent"]["source"].endswith("/agents/@example/example_agent.py")
    assert len(state["requests"]) == 1
    assert all("authorization" not in request.headers for request in state["requests"])
    assert client.perform(action="search", query="no matching capability")["total"] == 0


async def test_verified_install_hot_loads_for_only_the_authenticated_user(rar, settings):
    client, _ = rar
    result = install(client)
    assert result["status"] == "success"
    assert result["sha256"] == source_hash(SOURCE)
    registry = AgentRegistry(
        settings.agents_path, state_path=settings.state_path, include_defaults=True
    )
    owner = {agent.name: agent for agent in registry.load(user_id="42")}
    other = {agent.name for agent in registry.load(user_id="84")}
    assert await owner["Example"].invoke({}) == "example-ready"
    assert "Example" not in other
    assert not (settings.agents_path / "source_agent.py").exists()
    assert install(client)["alreadyInstalled"] is True
    assert any(
        entry["name"] == "@example/example_agent" and not entry["bundled"]
        for entry in client.perform(action="list_installed")["agents"]
    )


def test_install_requires_explicit_confirmation_before_network(rar):
    client, state = rar
    with pytest.raises(ToolExecutionError, match="confirm"):
        client.perform(action="install", agent_name="@example/example_agent")
    assert state["requests"] == []
    assert not client.install_path.exists()


@pytest.mark.parametrize("bad_hash", [None, "", "0" * 64])
def test_unverified_source_is_never_installed(rar, bad_hash):
    client, state = rar
    state["entry"]["_sha256"] = bad_hash
    with pytest.raises(ToolExecutionError, match="hash"):
        install(client)
    assert not list(client.install_path.rglob("source_agent.py"))


def test_rar_lf_hash_contract_supports_crlf_downloads(rar):
    client, state = rar
    state["source"] = SOURCE.replace(b"\n", b"\r\n")
    assert install(client)["sha256"] == source_hash(SOURCE)


@pytest.mark.parametrize(
    "source_path",
    [
        "agents/../../elsewhere_agent.py",
        "agents/%2e%2e/elsewhere_agent.py",
        "/agents/elsewhere_agent.py",
        "https://elsewhere.test/agent.py",
        "agents/elsewhere_agent.py?redirect=1",
    ],
)
def test_registry_cannot_redirect_source_downloads(rar, source_path):
    client, state = rar
    state["entry"]["_file"] = source_path
    with pytest.raises(ToolExecutionError, match="source path"):
        install(client)
    assert len(state["requests"]) == 1
    assert not list(client.install_path.rglob("source_agent.py"))


def test_catalog_failure_is_explicit_not_an_empty_search(rar):
    client, state = rar
    state["status"] = 503
    with pytest.raises(ToolExecutionError, match="could not be reached"):
        client.perform(action="search", query="memory")


def test_source_manifest_must_match_requested_package(rar):
    client, state = rar
    state["source"] = SOURCE.replace(b"@example/example_agent", b"@someone/other_agent")
    state["entry"]["_sha256"] = source_hash(state["source"])
    with pytest.raises(ToolExecutionError, match="manifest does not match"):
        install(client)
    assert not list(client.install_path.rglob("source_agent.py"))


def test_missing_python_dependency_does_not_admit_broken_package(rar):
    client, state = rar
    state["source"] = b"import nonexistent_brainstem_test_dependency\n" + SOURCE
    state["entry"]["_sha256"] = source_hash(state["source"])
    with pytest.raises(ToolExecutionError, match="needs Python dependency"):
        install(client)
    assert not list(client.install_path.rglob("source_agent.py"))


def test_new_install_cannot_replace_a_default_tool(rar):
    client, state = rar
    state["source"] = SOURCE.replace(b'"Example"', b'"ManageMemory"')
    state["entry"]["_sha256"] = source_hash(state["source"])
    with pytest.raises(ToolExecutionError, match="conflict"):
        install(client)
    assert not list(client.install_path.rglob("source_agent.py"))


def test_tampered_install_is_disabled_and_reported_without_disabling_defaults(rar, settings):
    client, _ = rar
    install(client)
    source = next(client.install_path.glob("*/source_agent.py"))
    source.write_text('raise RuntimeError("must never execute")\n', encoding="utf-8")
    status = BrainstemService(settings).status(GitHubIdentity("42", "octocat"))
    assert not status["brainstemReady"]
    assert "Example" not in {agent["name"] for agent in status["agents"]}
    assert status["loadedAgentCount"] == 4
    assert len(status["agentErrors"]) == 1
    assert "refusing to load" in status["agentErrors"][0]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("type", "stub", "Private RAR stubs"),
        ("dependencies", ["@example/another_agent"], "other RAR packages"),
    ],
)
def test_unsupported_packages_are_reported_before_download(rar, field, value, message):
    client, state = rar
    state["entry"][field] = value
    with pytest.raises(ToolExecutionError, match=message):
        install(client)
    assert len(state["requests"]) == 1


def test_default_packages_are_available_without_fetching_the_catalog(settings):
    client = RARClient(UserStorageManager(settings.state_path, "42"))
    installed = client.perform(action="list_installed")
    assert {entry["name"] for entry in installed["agents"]} == {
        "@rapp/basic_agent",
        "@rapp/hacker_news",
        "@kody-w/context_memory_agent",
        "@kody-w/manage_memory_agent",
    }
    assert (
        client.perform(action="install", agent_name="@rapp/hacker_news", confirm=True)[
            "alreadyInstalled"
        ]
        is True
    )
