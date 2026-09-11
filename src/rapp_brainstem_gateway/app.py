from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth import GitHubAuthenticator
from .brainstem import BrainstemService
from .config import Settings
from .errors import GatewayError, InvalidRequestError, MethodNotFoundError
from .protocol import TOOLS, initialize_result, require_tool, validate_request

logger = logging.getLogger("rapp_brainstem_gateway")


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
) -> Starlette:
    active_settings = settings or Settings.from_env()
    active_authenticator = authenticator or GitHubAuthenticator(active_settings)
    active_brainstem = brainstem or BrainstemService(active_settings)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "rapp-brainstem",
                "version": "0.1.0",
            }
        )

    async def mcp(request: Request) -> Response:
        request_id: Any = None
        try:
            try:
                payload = await request.json()
            except json.JSONDecodeError as exc:
                raise InvalidRequestError("Request body must be valid JSON.") from exc

            request_id, method, params = validate_request(payload)
            if method.startswith("notifications/"):
                return Response(status_code=202)
            if method == "initialize":
                return jsonrpc_result(request_id, initialize_result())
            if method == "ping":
                return jsonrpc_result(request_id, {})

            token = active_authenticator.bearer_token(request.headers.get("authorization"))
            identity = await active_authenticator.identify(token)

            if method == "tools/list":
                return jsonrpc_result(request_id, {"tools": TOOLS})
            if method != "tools/call":
                raise MethodNotFoundError(f"Unsupported MCP method: {method}")

            tool_name, arguments = require_tool(params)
            if tool_name == "brainstem_status":
                result = active_brainstem.status(identity)
            else:
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

            return jsonrpc_result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "structuredContent": result,
                    "isError": False,
                },
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
