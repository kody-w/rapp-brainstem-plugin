from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import __version__
from .auth import GitHubAuthenticator
from .brainstem import BrainstemService
from .config import Settings
from .errors import (
    GatewayError,
    InvalidRequestError,
    MethodNotFoundError,
    RequestDeadlineError,
)
from .protocol import (
    RAPP_WORK_TOOL_OPERATIONS,
    TOOLS,
    initialize_result,
    require_tool,
    validate_request,
)
from .rapp_work import DuplicateKeyError, RappWorkService, strict_json_loads

logger = logging.getLogger("rapp_brainstem_gateway")
MAX_MCP_REQUEST_BYTES = 1_048_576


def jsonrpc_result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def jsonrpc_error(request_id: Any, error: GatewayError) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": error.code, "message": str(error)},
        },
        status_code=error.status_code,
    )


def create_app(
    *,
    settings: Settings | None = None,
    authenticator: GitHubAuthenticator | None = None,
    brainstem: BrainstemService | None = None,
    rapp_work: RappWorkService | None = None,
) -> Starlette:
    active_settings = settings or Settings.from_env()
    active_authenticator = authenticator or GitHubAuthenticator(active_settings)
    active_brainstem = brainstem or BrainstemService(active_settings)
    active_rapp_work = rapp_work or RappWorkService(active_settings)

    async def health(_: Request) -> JSONResponse:
        deadline = time.monotonic() + min(active_settings.request_timeout_seconds, 3)
        readiness = await active_rapp_work.readiness(deadline=deadline)
        ready = readiness["ready"]
        return JSONResponse(
            {
                "status": "ready" if ready else "not-ready",
                "service": "rapp-brainstem",
                "version": __version__,
                "rappWork": readiness,
            },
            status_code=200 if ready else 503,
        )

    async def mcp(request: Request) -> Response:
        request_id: Any = None
        deadline = time.monotonic() + active_settings.request_timeout_seconds
        try:
            try:
                chunks: list[bytes] = []
                total = 0
                async with asyncio.timeout_at(deadline):
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > MAX_MCP_REQUEST_BYTES:
                            raise InvalidRequestError(
                                "Request body exceeds the one MiB limit."
                            )
                        chunks.append(chunk)
                payload = strict_json_loads(b"".join(chunks))
            except (json.JSONDecodeError, DuplicateKeyError, UnicodeDecodeError, ValueError) as exc:
                raise InvalidRequestError(
                    "Request body must be unambiguous, standards-compliant JSON."
                ) from exc

            request_id, method, params = validate_request(payload)
            if method.startswith("notifications/"):
                return Response(status_code=202)
            if method == "initialize":
                return jsonrpc_result(request_id, initialize_result())
            if method == "ping":
                return jsonrpc_result(request_id, {})

            token = active_authenticator.bearer_token(request.headers.get("authorization"))
            async with asyncio.timeout_at(deadline):
                identity = await active_authenticator.identify(token)

            if method == "tools/list":
                return jsonrpc_result(request_id, {"tools": TOOLS})
            if method != "tools/call":
                raise MethodNotFoundError(f"Unsupported MCP method: {method}")

            tool_name, arguments = require_tool(params)
            if tool_name == "brainstem_status":
                result = active_brainstem.status(identity)
            elif tool_name == "brainstem":
                async with asyncio.timeout_at(deadline):
                    result_value = await active_brainstem.chat(
                        identity=identity,
                        github_token=token,
                        request=str(arguments.get("request", "")),
                        requested_session_id=arguments.get("sessionId"),
                    )
                result = {
                    "response": result_value.response,
                    "sessionId": result_value.session_id,
                    "loadedAgentCount": result_value.loaded_agent_count,
                }
            else:
                result = await active_rapp_work.execute(
                    RAPP_WORK_TOOL_OPERATIONS[tool_name],
                    arguments,
                    principal=identity.id,
                    deadline=deadline,
                )

            return jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "structuredContent": result,
                    "isError": False,
                },
            )
        except TimeoutError:
            return jsonrpc_error(
                request_id,
                RequestDeadlineError("The request exceeded its absolute deadline."),
            )
        except GatewayError as exc:
            return jsonrpc_error(request_id, exc)
        except Exception:
            logger.exception("Unhandled Brainstem gateway error")
            return jsonrpc_error(request_id, GatewayError("Brainstem encountered an error."))

    @asynccontextmanager
    async def lifespan(_: Starlette):
        active_settings.state_path.mkdir(parents=True, exist_ok=True)
        yield

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/mcp", mcp, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main() -> None:
    uvicorn.run(
        "rapp_brainstem_gateway.app:app",
        host="0.0.0.0",
        port=7071,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
