from __future__ import annotations

from types import SimpleNamespace

import copilot

from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemService
from rapp_brainstem_gateway.storage import UserStorageManager


class FakeRuntime:
    def __init__(self):
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return "Brainstem response"


async def test_passes_user_token_to_isolated_runtime(settings):
    runtime = FakeRuntime()
    service = BrainstemService(settings, runtime=runtime)
    identity = GitHubIdentity(id="42", login="octocat")

    result = await service.chat(
        identity=identity,
        github_token="user-token",
        request="Do the work",
        requested_session_id="conversation",
    )

    assert result.response == "Brainstem response"
    assert result.session_id == "conversation"
    call = runtime.calls[0]
    assert call["identity"] == identity
    assert call["github_token"] == "user-token"
    assert call["session_id"].startswith("rapp-")
    assert "test Brainstem" in call["soul"]


async def test_same_public_session_is_different_for_each_user(settings):
    runtime = FakeRuntime()
    service = BrainstemService(settings, runtime=runtime)

    for user_id in ("42", "84"):
        await service.chat(
            identity=GitHubIdentity(id=user_id, login=f"user-{user_id}"),
            github_token=f"token-{user_id}",
            request="Continue",
            requested_session_id="same-session",
        )

    assert runtime.calls[0]["session_id"] != runtime.calls[1]["session_id"]


async def test_returned_session_id_can_be_resumed(settings):
    runtime = FakeRuntime()
    service = BrainstemService(settings, runtime=runtime)
    identity = GitHubIdentity(id="42", login="octocat")

    first = await service.chat(
        identity=identity,
        github_token="token",
        request="Start",
        requested_session_id=None,
    )
    second = await service.chat(
        identity=identity,
        github_token="token",
        request="Continue",
        requested_session_id=first.session_id,
    )

    assert first.session_id == second.session_id
    assert runtime.calls[0]["session_id"] == runtime.calls[1]["session_id"]


async def test_load_failures_are_returned_to_host_and_model(settings):
    (settings.agents_path / "broken_agent.py").write_text(
        "import nonexistent_brainstem_agent_dependency\n", encoding="utf-8"
    )
    runtime = FakeRuntime()
    result = await BrainstemService(settings, runtime=runtime).chat(
        identity=GitHubIdentity("42", "octocat"),
        github_token="user-token",
        request="Use my Brainstem",
        requested_session_id=None,
    )
    assert result.loaded_agent_count == 4
    assert len(result.agent_errors) == 1
    assert "missing Python dependency" in result.agent_errors[0]
    assert result.agent_errors[0] in runtime.calls[0]["soul"]


async def test_sdk_handler_runs_memory_and_propagates_canonical_failures(settings, monkeypatch):
    tool_results = []
    client_options = []

    class Session:
        def __init__(self, tools):
            self.tools = {tool.name: tool for tool in tools}

        async def send_and_wait(self, prompt):
            tool = self.tools["ManageMemory"]
            for content in ("Persist through the real SDK handler", ""):
                tool_results.append(
                    await tool.handler(
                        SimpleNamespace(arguments={"memory_type": "fact", "content": content})
                    )
                )
            return SimpleNamespace(data=SimpleNamespace(content="Memory result"))

        async def disconnect(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            client_options.append(kwargs)

        async def start(self):
            pass

        async def stop(self):
            pass

        async def list_sessions(self):
            return []

        async def create_session(self, **kwargs):
            return Session(kwargs["tools"])

    monkeypatch.setattr(copilot, "CopilotClient", Client)
    result = await BrainstemService(settings).chat(
        identity=GitHubIdentity("42", "octocat"),
        github_token="only-this-users-token",
        request="Remember this",
        requested_session_id=None,
    )
    assert result.response == "Memory result"
    assert client_options[0]["github_token"] == "only-this-users-token"
    assert client_options[0]["use_logged_in_user"] is False
    assert [result.result_type for result in tool_results] == ["success", "failure"]
    assert "No content provided" in tool_results[1].text_result_for_llm
    assert len(UserStorageManager(settings.state_path, "42").read_json()) == 1
