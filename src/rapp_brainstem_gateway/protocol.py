from __future__ import annotations

import re
from typing import Any

from .errors import InvalidRequestError, MethodNotFoundError

PROTOCOL_VERSION = "2025-06-18"

_PATH_PROPERTY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 4096,
    "description": (
        "Workspace root. Relative paths resolve beneath the first configured "
        "RAPP_WORK_ROOTS entry."
    ),
}
_OFFLINE_PROPERTY = {
    "type": "boolean",
    "const": True,
    "default": True,
    "description": "This integration permits only the canonical offline execution path.",
}
_APPLY_PROPERTY = {
    "type": "boolean",
    "default": False,
    "description": "Apply an already reviewed plan. Defaults to dry-run.",
}
_PLAN_DIGEST_PROPERTY = {
    "type": "string",
    "pattern": "^[0-9a-f]{64}$",
    "description": "Exact digest returned by the matching dry-run plan.",
}


def _closed_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _mutating_schema(
    properties: dict[str, Any],
    *,
    required: list[str],
) -> dict[str, Any]:
    schema = _closed_schema(
        {
            **properties,
            "offline": _OFFLINE_PROPERTY,
            "apply": _APPLY_PROPERTY,
            "planDigest": _PLAN_DIGEST_PROPERTY,
        }
    )
    schema["required"] = required
    schema["allOf"] = [
        {
            "if": {
                "properties": {"apply": {"const": True}},
                "required": ["apply"],
            },
            "then": {"required": ["planDigest"]},
        },
        {
            "if": {"required": ["planDigest"]},
            "then": {
                "properties": {"apply": {"const": True}},
                "required": ["apply"],
            },
        },
    ]
    return schema


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
    {
        "name": "rapp_work_status",
        "description": "Read sanitized RAPP Work readiness without disclosing roots.",
        "inputSchema": _closed_schema(
            {
                "root": _PATH_PROPERTY,
                "offline": _OFFLINE_PROPERTY,
            }
        ),
    },
    {
        "name": "rapp_work_verify",
        "description": "Verify a workspace through the canonical RAPP Work SDK.",
        "inputSchema": _closed_schema(
            {
                "root": _PATH_PROPERTY,
                "offline": _OFFLINE_PROPERTY,
            }
        ),
    },
    {
        "name": "rapp_work_discover",
        "description": "Discover RAPP Work resources through the canonical SDK.",
        "inputSchema": {
            **_closed_schema(
                {
                    "roots": {
                        "type": "array",
                        "items": _PATH_PROPERTY,
                        "minItems": 1,
                        "maxItems": 32,
                        "description": (
                            "One to thirty-two allowlisted roots for inert discovery."
                        ),
                    },
                    "maxEntries": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10_000,
                        "default": 10_000,
                        "description": "Maximum filesystem entries inspected across the roots.",
                    },
                    "offline": _OFFLINE_PROPERTY,
                }
            ),
            "required": ["roots"],
        },
    },
    {
        "name": "rapp_work_scaffold",
        "description": "Plan or explicitly apply canonical RAPP Work scaffolding.",
        "inputSchema": _mutating_schema(
            {
                "root": _PATH_PROPERTY,
                "kind": {
                    "type": "string",
                    "enum": ["workspace", "organization"],
                    "description": "Canonical workspace kind to create.",
                },
                "ownerLabel": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 39,
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    "description": "Lowercase RAPP owner label.",
                },
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    "description": "Lowercase workspace or organization slug.",
                },
                "worldId": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    "description": "Immutable lowercase world boundary identifier.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["solo", "hive"],
                    "default": "solo",
                    "description": "Workspace operating mode.",
                },
            },
            required=["root", "kind", "ownerLabel", "slug", "worldId", "mode"],
        ),
    },
    {
        "name": "rapp_work_update",
        "description": "Plan or explicitly apply a canonical RAPP Work update.",
        "inputSchema": _mutating_schema(
            {
                "root": _PATH_PROPERTY,
            },
            required=["root"],
        ),
    },
    {
        "name": "rapp_work_migrate",
        "description": "Plan or explicitly apply a canonical RAPP Work migration.",
        "inputSchema": _mutating_schema(
            {
                "source": {
                    **_PATH_PROPERTY,
                    "description": "Existing migration source within an allowed root.",
                },
                "target": {
                    **_PATH_PROPERTY,
                    "description": "Create-only migration target within an allowed root.",
                },
            },
            required=["source", "target"],
        ),
    },
]

RAPP_WORK_TOOL_OPERATIONS = {
    "rapp_work_status": "status",
    "rapp_work_verify": "verify",
    "rapp_work_discover": "discover",
    "rapp_work_scaffold": "scaffold",
    "rapp_work_update": "update",
    "rapp_work_migrate": "migrate",
}
RAPP_WORK_MUTATORS = frozenset({"scaffold", "update", "migrate"})


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
    from . import __version__

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "rapp-brainstem", "version": __version__},
    }


def require_tool(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise InvalidRequestError("Tool name and arguments are required.")
    known = {tool["name"] for tool in TOOLS}
    if name not in known:
        raise MethodNotFoundError(f"Unknown Brainstem tool: {name}")
    validate_tool_arguments(name, arguments)
    return name, arguments


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
    properties = schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise InvalidRequestError(
            f"Unknown argument(s) for {name}: {', '.join(unknown)}."
        )
    for required in schema.get("required", []):
        if required not in arguments:
            raise InvalidRequestError(f"Missing required argument for {name}: {required}.")
    for key, value in arguments.items():
        property_schema = properties[key]
        expected_type = property_schema.get("type")
        if expected_type == "string":
            if not isinstance(value, str):
                raise InvalidRequestError(f"{name}.{key} must be a string.")
            length = len(value)
            if length < property_schema.get("minLength", 0):
                raise InvalidRequestError(f"{name}.{key} cannot be empty.")
            if length > property_schema.get("maxLength", length):
                raise InvalidRequestError(f"{name}.{key} is too long.")
            pattern = property_schema.get("pattern")
            if pattern and re.fullmatch(pattern, value) is None:
                raise InvalidRequestError(f"{name}.{key} has an invalid format.")
        elif expected_type == "boolean" and not isinstance(value, bool):
            raise InvalidRequestError(f"{name}.{key} must be a boolean.")
        elif expected_type == "integer":
            if type(value) is not int:
                raise InvalidRequestError(f"{name}.{key} must be an integer.")
            if value < property_schema.get("minimum", value):
                raise InvalidRequestError(f"{name}.{key} is too small.")
            if value > property_schema.get("maximum", value):
                raise InvalidRequestError(f"{name}.{key} is too large.")
        elif expected_type == "array":
            if not isinstance(value, list):
                raise InvalidRequestError(f"{name}.{key} must be an array.")
            if len(value) < property_schema.get("minItems", 0):
                raise InvalidRequestError(f"{name}.{key} has too few entries.")
            if len(value) > property_schema.get("maxItems", len(value)):
                raise InvalidRequestError(f"{name}.{key} has too many entries.")
            item_schema = property_schema.get("items", {})
            if item_schema.get("type") == "string":
                for item in value:
                    if (
                        not isinstance(item, str)
                        or len(item) < item_schema.get("minLength", 0)
                        or len(item) > item_schema.get("maxLength", len(item))
                    ):
                        raise InvalidRequestError(
                            f"Every {name}.{key} entry must be a valid string."
                        )
        if "enum" in property_schema and value not in property_schema["enum"]:
            raise InvalidRequestError(f"{name}.{key} has an unsupported value.")
        if "const" in property_schema and value != property_schema["const"]:
            raise InvalidRequestError(f"{name}.{key} has an unsupported value.")

    operation = RAPP_WORK_TOOL_OPERATIONS.get(name)
    if (
        operation in RAPP_WORK_MUTATORS
        and arguments.get("apply")
        and "planDigest" not in arguments
    ):
        raise InvalidRequestError(
            f"{name}.planDigest is required when apply is true."
        )
    if (
        operation in RAPP_WORK_MUTATORS
        and not arguments.get("apply", False)
        and "planDigest" in arguments
    ):
        raise InvalidRequestError(
            f"{name}.planDigest is accepted only when apply is true."
        )
