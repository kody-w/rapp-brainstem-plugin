from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from .agents import AgentRegistry, LoadedAgent
from .auth import GitHubIdentity
from .config import Settings
from .errors import ToolExecutionError

_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class BrainstemResult:
    response: str
    session_id: str
    loaded_agent_count: int


class Runtime(Protocol):
    async def chat(
        self,
        *,
        identity: GitHubIdentity,
        github_token: str,
        prompt: str,
        session_id: str,
        soul: str,
        agents: list[LoadedAgent],
    ) -> str: ...


class CopilotRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def chat(
        self,
        *,
        identity: GitHubIdentity,
        github_token: str,
        prompt: str,
        session_id: str,
        soul: str,
        agents: list[LoadedAgent],
    ) -> str:
        from copilot import CopilotClient, ToolSet
        from copilot.session import PermissionHandler
        from copilot.tools import Tool, ToolInvocation, ToolResult

        tools = []
        for agent in agents:
            async def invoke(
                invocation: ToolInvocation,
                *,
                loaded_agent: LoadedAgent = agent,
            ) -> ToolResult:
                try:
                    value = loaded_agent.instance.perform(**invocation.arguments)
                    if inspect.isawaitable(value):
                        value = await value
                    text = value if isinstance(value, str) else json.dumps(value, default=str)
                    return ToolResult(
                        text_result_for_llm=text,
                        result_type="success",
                        session_log=f"Ran {loaded_agent.name}",
                    )
                except Exception as exc:
                    return ToolResult(
                        text_result_for_llm=f"{loaded_agent.name} failed: {exc}",
                        result_type="failure",
                        session_log=f"{loaded_agent.name} failed",
                    )

            tools.append(
                Tool(
                    name=agent.name,
                    description=agent.description,
                    parameters=agent.parameters,
                    handler=invoke,
                )
            )

        user_home = self._settings.state_path / identity.id
        user_home.mkdir(parents=True, exist_ok=True)
        client = CopilotClient(
            github_token=github_token,
            use_logged_in_user=False,
            base_directory=str(user_home),
            mode="empty",
        )
        try:
            await client.start()
            session_options = {
                "model": self._settings.model,
                "tools": tools,
                "available_tools": ToolSet().add_custom("*"),
                "system_message": {"mode": "append", "content": soul},
                "enable_session_store": False,
                "on_permission_request": PermissionHandler.approve_all,
            }
            known_sessions = {item.session_id for item in await client.list_sessions()}
            if session_id in known_sessions:
                session = await client.resume_session(session_id, **session_options)
            else:
                session = await client.create_session(
                    session_id=session_id,
                    **session_options,
                )
            try:
                response = await session.send_and_wait(prompt)
                if response is None:
                    raise ToolExecutionError("Brainstem completed without a response.")
                content = getattr(getattr(response, "data", None), "content", None)
                if not isinstance(content, str) or not content:
                    raise ToolExecutionError("Brainstem returned an invalid response.")
                return content
            finally:
                await session.disconnect()
        finally:
            await client.stop()


class BrainstemService:
    def __init__(
        self,
        settings: Settings,
        *,
        runtime: Runtime | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._runtime = runtime or CopilotRuntime(settings)
        self._registry = registry or AgentRegistry(settings.agents_path)
        self._session_locks: dict[str, asyncio.Lock] = {}

    def status(self, identity: GitHubIdentity) -> dict[str, Any]:
        return {
            "connected": True,
            "githubLogin": identity.login,
            "brainstemReady": self._settings.soul_path.exists(),
            "loadedAgentCount": len(self._registry.load()),
        }

    async def chat(
        self,
        *,
        identity: GitHubIdentity,
        github_token: str,
        request: str,
        requested_session_id: str | None,
    ) -> BrainstemResult:
        prompt = request.strip()
        if not prompt:
            raise ToolExecutionError("The Brainstem request cannot be empty.")
        if len(prompt) > 50_000:
            raise ToolExecutionError("The Brainstem request is too large.")

        public_session_id = requested_session_id or f"brainstem-{secrets.token_hex(12)}"
        session_id = self._session_id(identity.id, public_session_id)
        soul = self._read_soul()
        agents = self._registry.load()
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        try:
            async with lock:
                response = await asyncio.wait_for(
                    self._runtime.chat(
                        identity=identity,
                        github_token=github_token,
                        prompt=prompt,
                        session_id=session_id,
                        soul=soul,
                        agents=agents,
                    ),
                    timeout=self._settings.request_timeout_seconds,
                )
        except TimeoutError as exc:
            raise ToolExecutionError(
                "Brainstem exceeded the Cowork request deadline. Retry with a smaller task."
            ) from exc

        return BrainstemResult(
            response=response,
            session_id=public_session_id,
            loaded_agent_count=len(agents),
        )

    def _read_soul(self) -> str:
        if not self._settings.soul_path.exists():
            raise ToolExecutionError("Brainstem soul.md is missing.")
        return self._settings.soul_path.read_text(encoding="utf-8")

    def _session_id(self, user_id: str, requested: str | None) -> str:
        public_id = requested or "default"
        if not _SESSION_PATTERN.fullmatch(public_id):
            raise ToolExecutionError("The session ID contains unsupported characters.")
        digest = hmac.new(
            self._settings.session_secret.encode(),
            f"{user_id}:{public_id}".encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"rapp-{digest}"
