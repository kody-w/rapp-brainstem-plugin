from __future__ import annotations

from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemService


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
