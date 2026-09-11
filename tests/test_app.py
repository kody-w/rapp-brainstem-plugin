from __future__ import annotations

from starlette.testclient import TestClient

from rapp_brainstem_gateway.app import create_app
from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemResult


class FakeAuthenticator:
    @staticmethod
    def bearer_token(header):
        if header != "Bearer good":
            from rapp_brainstem_gateway.errors import AuthenticationError

            raise AuthenticationError("Not connected")
        return "good"

    async def identify(self, token):
        assert token == "good"
        return GitHubIdentity(id="42", login="octocat")


class FakeBrainstem:
    @staticmethod
    def status(identity):
        return {
            "connected": True,
            "githubLogin": identity.login,
            "brainstemReady": True,
            "loadedAgentCount": 1,
        }

    async def chat(self, **kwargs):
        assert kwargs["github_token"] == "good"
        return BrainstemResult("done", "brainstem-session", 1)


def rpc(client, method, params=None, token=None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_health_is_anonymous(settings):
    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_initialize_is_anonymous(settings):
    with TestClient(create_app(settings=settings)) as client:
        response = rpc(client, "initialize")
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "rapp-brainstem"


def test_tools_require_github_authentication(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
    )
    with TestClient(app) as client:
        unauthorized = rpc(client, "tools/list")
        authorized = rpc(client, "tools/list", token="good")

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert {tool["name"] for tool in authorized.json()["result"]["tools"]} == {
        "brainstem",
        "brainstem_status",
    }


def test_calls_brainstem_with_authenticated_token(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
    )
    with TestClient(app) as client:
        response = rpc(
            client,
            "tools/call",
            {"name": "brainstem", "arguments": {"request": "Do it"}},
            token="good",
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["response"] == "done"
    assert result["isError"] is False
