from __future__ import annotations

import asyncio
import json
from dataclasses import replace

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


class FakeRappWork:
    def __init__(self):
        self.calls = []
        self.deadlines = []

    async def execute(self, operation, arguments, *, principal, deadline):
        self.calls.append((operation, arguments, principal))
        self.deadlines.append(deadline)
        return {"operation": operation, "status": "ok"}

    @staticmethod
    async def readiness(*, deadline):
        assert deadline > 0
        return {
            "authorizationConfigured": True,
            "ready": True,
            "sdkReady": True,
            "workspaceRootsReady": True,
        }


class SlowAuthenticator(FakeAuthenticator):
    async def identify(self, token):
        await asyncio.sleep(0.2)
        return await super().identify(token)


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
    with TestClient(create_app(settings=settings, rapp_work=FakeRappWork())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["rappWork"]["ready"] is True
    assert str(settings.root) not in response.text


def test_health_fails_closed_when_sdk_is_missing_without_leaking_roots(settings):
    missing = replace(
        settings,
        rapp_work_command=str(settings.root / "missing-rapp-work"),
    )

    with TestClient(create_app(settings=missing)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "not-ready"
    assert response.json()["rappWork"]["sdkReady"] is False
    assert str(settings.root) not in response.text


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
        "rapp_work_status",
        "rapp_work_verify",
        "rapp_work_discover",
        "rapp_work_scaffold",
        "rapp_work_update",
        "rapp_work_migrate",
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


def test_calls_rapp_work_after_bearer_authentication(settings):
    rapp_work = FakeRappWork()
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=rapp_work,
    )
    with TestClient(app) as client:
        response = rpc(
            client,
            "tools/call",
            {"name": "rapp_work_verify", "arguments": {"root": "."}},
            token="good",
        )

    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["status"] == "ok"
    assert rapp_work.calls == [("verify", {"root": "."}, "42")]
    assert len(rapp_work.deadlines) == 1


def test_authentication_and_tool_execution_share_one_request_deadline(settings):
    rapp_work = FakeRappWork()
    app = create_app(
        settings=replace(settings, request_timeout_seconds=0.05),
        authenticator=SlowAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=rapp_work,
    )

    with TestClient(app) as client:
        response = rpc(
            client,
            "tools/call",
            {"name": "rapp_work_status", "arguments": {}},
            token="good",
        )

    assert response.status_code == 504
    assert "absolute deadline" in response.json()["error"]["message"]
    assert rapp_work.calls == []
    assert str(settings.root) not in json.dumps(response.json())


def test_rejects_unknown_tool_arguments(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=FakeRappWork(),
    )
    with TestClient(app) as client:
        response = rpc(
            client,
            "tools/call",
            {"name": "rapp_work_status", "arguments": {"unexpected": True}},
            token="good",
        )

    assert response.status_code == 400
    assert "Unknown argument" in response.json()["error"]["message"]


def test_rejects_plan_digest_without_explicit_apply(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=FakeRappWork(),
    )
    with TestClient(app) as client:
        response = rpc(
            client,
            "tools/call",
            {
                "name": "rapp_work_update",
                "arguments": {"root": ".", "planDigest": "a" * 64},
            },
            token="good",
        )

    assert response.status_code == 400
    assert "only when apply is true" in response.json()["error"]["message"]


def test_rejects_duplicate_json_members(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=FakeRappWork(),
    )
    body = (
        '{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list","params":{}}'
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Authorization": "******",
                "Content-Type": "application/json",
            },
            content=body,
        )

    assert response.status_code == 400
    assert "unambiguous" in response.json()["error"]["message"]


def test_rejects_oversized_mcp_request_body(settings):
    app = create_app(
        settings=settings,
        authenticator=FakeAuthenticator(),
        brainstem=FakeBrainstem(),
        rapp_work=FakeRappWork(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            content=b" " * 1_048_577,
        )

    assert response.status_code == 400
    assert "one MiB" in response.json()["error"]["message"]
