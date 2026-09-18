from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_GITHUB_PRINCIPAL = re.compile(r"^[1-9][0-9]{0,19}$")


def _bounded_timeout(value: str, *, default: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError:
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return min(max(parsed, 0.1), maximum)


def _absolute_path(value: str, *, variable: str) -> Path:
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{variable} entries must be absolute paths")
    return path.resolve()


def _principal(value: Any, *, variable: str) -> str:
    if not isinstance(value, str) or _GITHUB_PRINCIPAL.fullmatch(value) is None:
        raise ValueError(f"{variable} must contain immutable numeric GitHub user IDs")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate RAPP_WORK_PRINCIPAL_ROOTS principal: {key}")
        result[key] = value
    return result


def _principal_roots(
    raw: str,
    *,
    configured_roots: tuple[Path, ...],
) -> tuple[tuple[str, tuple[Path, ...]], ...]:
    if not raw.strip():
        return ()
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError("RAPP_WORK_PRINCIPAL_ROOTS must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("RAPP_WORK_PRINCIPAL_ROOTS must be a JSON object")

    grants: list[tuple[str, tuple[Path, ...]]] = []
    for raw_principal, raw_roots in payload.items():
        principal = _principal(raw_principal, variable="RAPP_WORK_PRINCIPAL_ROOTS")
        if not isinstance(raw_roots, list) or not raw_roots:
            raise ValueError(
                "Each RAPP_WORK_PRINCIPAL_ROOTS principal must have at least one root"
            )
        roots: list[Path] = []
        for raw_root in raw_roots:
            if not isinstance(raw_root, str) or not raw_root.strip():
                raise ValueError("RAPP_WORK_PRINCIPAL_ROOTS roots must be strings")
            root = _absolute_path(raw_root, variable="RAPP_WORK_PRINCIPAL_ROOTS")
            if not any(
                root == configured or root.is_relative_to(configured)
                for configured in configured_roots
            ):
                raise ValueError(
                    "RAPP_WORK_PRINCIPAL_ROOTS entries must stay within RAPP_WORK_ROOTS"
                )
            if root not in roots:
                roots.append(root)
        grants.append((principal, tuple(roots)))
    return tuple(sorted(grants))


@dataclass(frozen=True)
class Settings:
    root: Path
    agents_path: Path
    soul_path: Path
    state_path: Path
    github_api_url: str
    github_api_version: str
    request_timeout_seconds: float
    model: str
    session_secret: str
    rapp_work_command: str | None = None
    rapp_work_roots: tuple[Path, ...] = ()
    rapp_work_owner_ids: frozenset[str] = frozenset()
    rapp_work_principal_roots: tuple[tuple[str, tuple[Path, ...]], ...] = ()
    rapp_work_timeout_seconds: float = 15
    rapp_work_max_output_bytes: int = 1_048_576

    @classmethod
    def from_env(cls) -> Settings:
        root = Path(os.getenv("RAPP_ROOT", Path.cwd())).resolve()
        session_secret = os.getenv("RAPP_SESSION_SECRET", "").strip()
        if not session_secret:
            session_secret = "development-only-change-me"
        request_timeout_seconds = _bounded_timeout(
            os.getenv("RAPP_REQUEST_TIMEOUT_SECONDS", "25"),
            default=25,
            maximum=25,
        )
        configured_roots = tuple(
            _absolute_path(value, variable="RAPP_WORK_ROOTS")
            for value in os.getenv("RAPP_WORK_ROOTS", "").split(os.pathsep)
            if value.strip()
        ) or (root,)
        owner_ids = frozenset(
            _principal(value.strip(), variable="RAPP_WORK_OWNER_IDS")
            for value in os.getenv("RAPP_WORK_OWNER_IDS", "").split(",")
            if value.strip()
        )
        principal_roots = _principal_roots(
            os.getenv("RAPP_WORK_PRINCIPAL_ROOTS", ""),
            configured_roots=configured_roots,
        )
        rapp_work_timeout_seconds = min(
            _bounded_timeout(
                os.getenv("RAPP_WORK_TIMEOUT_SECONDS", "15"),
                default=15,
                maximum=20,
            ),
            request_timeout_seconds,
        )
        rapp_work_max_output_bytes = min(
            max(int(os.getenv("RAPP_WORK_MAX_OUTPUT_BYTES", "1048576")), 1_024),
            4_194_304,
        )
        return cls(
            root=root,
            agents_path=Path(os.getenv("RAPP_AGENTS_PATH", root / "agents")).resolve(),
            soul_path=Path(os.getenv("RAPP_SOUL_PATH", root / "soul.md")).resolve(),
            state_path=Path(os.getenv("RAPP_STATE_PATH", root / "copilot-home")).resolve(),
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            github_api_version=os.getenv("GITHUB_API_VERSION", "2022-11-28"),
            request_timeout_seconds=request_timeout_seconds,
            model=os.getenv("RAPP_MODEL", "gpt-5.4"),
            session_secret=session_secret,
            rapp_work_command=os.getenv("RAPP_WORK_COMMAND", "").strip() or None,
            rapp_work_roots=configured_roots,
            rapp_work_owner_ids=owner_ids,
            rapp_work_principal_roots=principal_roots,
            rapp_work_timeout_seconds=rapp_work_timeout_seconds,
            rapp_work_max_output_bytes=rapp_work_max_output_bytes,
        )
