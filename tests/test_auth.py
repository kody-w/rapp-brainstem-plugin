from __future__ import annotations

import httpx
import pytest

from rapp_brainstem_gateway.auth import GitHubAuthenticator
from rapp_brainstem_gateway.errors import AuthenticationError


def test_requires_bearer_token(settings):
    authenticator = GitHubAuthenticator(settings)
    with pytest.raises(AuthenticationError):
        authenticator.bearer_token(None)
    with pytest.raises(AuthenticationError):
        authenticator.bearer_token("Basic abc")
    assert authenticator.bearer_token("Bearer secret") == "secret"


async def test_resolves_and_caches_github_identity(settings):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"id": 42, "login": "octocat"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        authenticator = GitHubAuthenticator(settings, client=client)
        first = await authenticator.identify("secret")
        second = await authenticator.identify("secret")

    assert first.id == "42"
    assert second.login == "octocat"
    assert calls == 1


async def test_rejects_invalid_github_token(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401))
    ) as client:
        authenticator = GitHubAuthenticator(settings, client=client)
        with pytest.raises(AuthenticationError):
            await authenticator.identify("bad-token")
