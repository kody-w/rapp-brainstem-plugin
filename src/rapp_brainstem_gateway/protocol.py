from __future__ import annotations

from typing import Any

from .errors import InvalidRequestError, MethodNotFoundError

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "brainstem",
        "description": "Ask the authenticated user's RAPP Brainstem to perform a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The task or question for the user's Brainstem.",
                },
                "sessionId": {
                    "type": "string",
                    "description": "Optional Brainstem conversation session to continue.",
                },
            },
            "required": ["request"],
            "additionalProperties": False,
        },
    },
    {
        "name": "brainstem_status",
        "description": "Check the authenticated user's Brainstem and GitHub connection.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def validate_request(payload: Any) -> tuple[Any, str, dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        raise InvalidRequestError("Expected a JSON-RPC 2.0 request.")
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        raise InvalidRequestError("The JSON-RPC method and params are invalid.")
    return request_id, method, params


def initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "rapp-brainstem", "version": "0.1.0"},
    }


def require_tool(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise InvalidRequestError("Tool name and arguments are required.")
    known = {tool["name"] for tool in TOOLS}
    if name not in known:
        raise MethodNotFoundError(f"Unknown Brainstem tool: {name}")
    return name, arguments
