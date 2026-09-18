from __future__ import annotations

import asyncio
import hmac
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ToolExecutionError
from .protocol import RAPP_WORK_MUTATORS

_MAX_PLAN_BYTES = 1_048_576
_MAX_REVIEWED_PLANS = 32
_PLAN_TTL_SECONDS = 15 * 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_PRINCIPAL = re.compile(r"^[1-9][0-9]{0,19}$")
RAPP_WORK_SDK_VERSION = "1.0.0"
_PATH_KEYS = frozenset(
    {
        "changes",
        "credential",
        "credentials",
        "creates",
        "deletes",
        "destination",
        "destinations",
        "file",
        "files",
        "moves",
        "operations",
        "path",
        "paths",
        "root",
        "source",
        "sources",
        "target",
        "targets",
        "updates",
        "writes",
    }
)


class DuplicateKeyError(ValueError):
    pass


class _OutputLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ReviewedPlan:
    request_json: bytes
    plan_json: bytes
    expires_at: float


def strict_json_loads(value: bytes | str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DuplicateKeyError(f"Duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> None:
        raise ValueError(f"Invalid JSON constant: {constant}")

    def reject_float(_: str) -> None:
        raise ValueError("Floating-point JSON values are not supported.")

    return json.loads(
        value,
        object_pairs_hook=reject_duplicates,
        parse_float=reject_float,
        parse_constant=reject_constant,
    )


class RappWorkService:
    def __init__(
        self,
        settings: Settings,
        *,
        command: str | None = None,
        roots: tuple[Path, ...] | None = None,
    ) -> None:
        self._settings = settings
        self._command = command if command is not None else settings.rapp_work_command
        configured_roots = roots if roots is not None else settings.rapp_work_roots
        self._roots = tuple(
            root.expanduser().resolve() for root in (configured_roots or (settings.root,))
        )
        configured_timeout = float(settings.rapp_work_timeout_seconds)
        gateway_timeout = float(settings.request_timeout_seconds)
        self._request_timeout_seconds = (
            min(max(gateway_timeout, 0.1), 25)
            if math.isfinite(gateway_timeout)
            else 25
        )
        self._timeout_seconds = (
            min(max(configured_timeout, 0.1), max(gateway_timeout, 0.1), 20)
            if math.isfinite(configured_timeout) and math.isfinite(gateway_timeout)
            else 15
        )
        self._max_output_bytes = min(
            max(int(settings.rapp_work_max_output_bytes), 1_024),
            4_194_304,
        )
        self._owner_ids = frozenset(settings.rapp_work_owner_ids)
        self._principal_roots = {
            principal: tuple(root.expanduser().resolve() for root in principal_roots)
            for principal, principal_roots in settings.rapp_work_principal_roots
        }
        for principal_roots in self._principal_roots.values():
            if not all(
                any(root == allowed or root.is_relative_to(allowed) for allowed in self._roots)
                for root in principal_roots
            ):
                raise ValueError(
                    "RAPP Work principal roots must stay within configured roots"
                )
        self._version_checked = False
        self._version_lock = asyncio.Lock()
        self._reviewed_plans: dict[tuple[str, str, str], _ReviewedPlan] = {}
        self._plan_lock = asyncio.Lock()

    async def execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        principal: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        absolute_deadline = self._absolute_deadline(deadline)
        try:
            async with asyncio.timeout_at(absolute_deadline):
                return await self._execute(
                    operation,
                    arguments,
                    principal=principal,
                    deadline=absolute_deadline,
                )
        except TimeoutError as exc:
            raise ToolExecutionError(
                "The RAPP Work request exceeded its absolute deadline."
            ) from exc

    async def _execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        principal: str,
        deadline: float,
    ) -> dict[str, Any]:
        if operation not in {
            "status",
            "verify",
            "discover",
            "scaffold",
            "update",
            "migrate",
        }:
            raise ToolExecutionError(f"Unsupported RAPP Work operation: {operation}.")
        authorized_roots = self._authorized_roots(principal)
        if arguments.get("offline", True) is not True:
            raise ToolExecutionError(
                "This RAPP Work integration permits only the canonical offline path."
            )
        if not arguments.get("apply", False) and "planDigest" in arguments:
            raise ToolExecutionError(
                "A RAPP Work plan digest is accepted only with explicit apply."
            )
        plan_request = self._request(
            operation,
            arguments,
            apply=False,
            roots=authorized_roots,
        )
        await self._ensure_compatible_version(deadline)
        if operation not in RAPP_WORK_MUTATORS:
            return await self._invoke(plan_request, deadline)

        if not arguments.get("apply", False):
            result = await self._invoke(plan_request, deadline)
            plan, plan_digest = self._reviewed_plan(result)
            self._reject_protected_plan(plan)
            await self._remember_plan(
                principal,
                operation,
                plan_digest,
                plan_request,
                plan,
            )
            return result

        supplied_digest = arguments.get("planDigest")
        if not isinstance(supplied_digest, str) or not supplied_digest:
            raise ToolExecutionError("Apply requires the exact dry-run plan digest.")
        plan = await self._take_plan(
            principal,
            operation,
            supplied_digest,
            plan_request,
        )
        self._reject_protected_plan(plan)

        apply_request = self._request(
            operation,
            arguments,
            apply=True,
            roots=authorized_roots,
        )
        apply_request["plan"] = plan
        apply_request["plan_sha256"] = supplied_digest
        return await self._invoke(apply_request, deadline)

    async def readiness(self, *, deadline: float | None = None) -> dict[str, bool]:
        absolute_deadline = self._absolute_deadline(deadline)
        authorization_configured = bool(self._owner_ids or self._principal_roots)
        authorized_roots = {
            root
            for principal_roots in self._principal_roots.values()
            for root in principal_roots
        }
        if self._owner_ids:
            authorized_roots.update(self._roots)
        workspace_roots_ready = bool(authorized_roots) and all(
            root.is_absolute() and root.is_dir() for root in authorized_roots
        )
        sdk_ready = False
        try:
            async with asyncio.timeout_at(absolute_deadline):
                await self._ensure_compatible_version(absolute_deadline)
            sdk_ready = True
        except (OSError, TimeoutError, ToolExecutionError):
            pass
        return {
            "authorizationConfigured": authorization_configured,
            "ready": authorization_configured and workspace_roots_ready and sdk_ready,
            "sdkReady": sdk_ready,
            "workspaceRootsReady": workspace_roots_ready,
        }

    def _absolute_deadline(self, deadline: float | None) -> float:
        if deadline is None:
            return time.monotonic() + self._request_timeout_seconds
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ToolExecutionError("The RAPP Work request deadline is invalid.")
        if deadline <= time.monotonic():
            raise ToolExecutionError(
                "The RAPP Work request exceeded its absolute deadline."
            )
        return float(deadline)

    def _authorized_roots(self, principal: str) -> tuple[Path, ...]:
        if (
            not isinstance(principal, str)
            or _GITHUB_PRINCIPAL.fullmatch(principal) is None
        ):
            raise ToolExecutionError(
                "RAPP Work requires an immutable numeric GitHub principal."
            )
        mapped = self._principal_roots.get(principal)
        if mapped is not None:
            return mapped
        if principal in self._owner_ids:
            return self._roots
        raise ToolExecutionError(
            "The authenticated GitHub principal is not authorized for RAPP Work."
        )

    async def _remember_plan(
        self,
        principal: str,
        operation: str,
        digest: str,
        request: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        if not isinstance(principal, str) or not principal or len(principal) > 256:
            raise ToolExecutionError("The authenticated RAPP Work principal is invalid.")
        request_json = self._canonical_json(request)
        plan_json = self._canonical_json(plan)
        now = time.monotonic()
        async with self._plan_lock:
            self._prune_plans(now)
            key = (principal, operation, digest)
            self._reviewed_plans[key] = _ReviewedPlan(
                request_json=request_json,
                plan_json=plan_json,
                expires_at=now + _PLAN_TTL_SECONDS,
            )
            while len(self._reviewed_plans) > _MAX_REVIEWED_PLANS:
                self._reviewed_plans.pop(next(iter(self._reviewed_plans)))

    async def _take_plan(
        self,
        principal: str,
        operation: str,
        digest: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.monotonic()
        async with self._plan_lock:
            self._prune_plans(now)
            reviewed = self._reviewed_plans.pop(
                (principal, operation, digest),
                None,
            )
        if reviewed is None:
            raise ToolExecutionError(
                "No current reviewed plan matches this principal, operation, and digest. "
                "Run the dry-run again."
            )
        if not hmac.compare_digest(reviewed.request_json, self._canonical_json(request)):
            raise ToolExecutionError(
                "The reviewed plan inputs do not match this apply request. "
                "Run the dry-run again."
            )
        plan = strict_json_loads(reviewed.plan_json)
        if not isinstance(plan, dict):
            raise ToolExecutionError("The reviewed RAPP Work plan is invalid.")
        return plan

    def _prune_plans(self, now: float) -> None:
        expired = [
            key
            for key, reviewed in self._reviewed_plans.items()
            if reviewed.expires_at <= now
        ]
        for key in expired:
            del self._reviewed_plans[key]

    @staticmethod
    def _canonical_json(value: dict[str, Any]) -> bytes:
        return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _request(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        apply: bool,
        roots: tuple[Path, ...],
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "operation": operation,
        }
        if operation == "discover":
            request["roots"] = [
                str(self._safe_path(value, field="roots", roots=roots))
                for value in arguments.get("roots", [])
            ]
            if "maxEntries" in arguments:
                request["max_entries"] = arguments["maxEntries"]
        elif operation == "migrate":
            request["source"] = str(
                self._safe_path(arguments.get("source"), field="source", roots=roots)
            )
            request["target"] = str(
                self._safe_path(arguments.get("target"), field="target", roots=roots)
            )
        else:
            request["root"] = str(
                self._safe_path(arguments.get("root"), field="root", roots=roots)
            )
        if operation == "scaffold":
            request.update(
                {
                    "kind": arguments["kind"],
                    "owner_label": arguments["ownerLabel"],
                    "slug": arguments["slug"],
                    "world_id": arguments["worldId"],
                    "mode": arguments.get("mode", "solo"),
                }
            )
        if operation in RAPP_WORK_MUTATORS:
            request["apply"] = apply
        return request

    def _safe_path(
        self,
        value: Any,
        *,
        field: str,
        roots: tuple[Path, ...],
    ) -> Path:
        if not roots:
            raise ToolExecutionError("No RAPP Work path roots are configured.")
        try:
            if value is None:
                candidate = roots[0]
            else:
                candidate = Path(str(value)).expanduser()
                if not candidate.is_absolute():
                    candidate = roots[0] / candidate
            lexical = Path(os.path.abspath(candidate))
            resolved = lexical.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolExecutionError(f"RAPP Work {field} is not a valid path.") from exc
        cursor = Path(lexical.anchor)
        for component in lexical.parts[1:]:
            cursor /= component
            if cursor.is_symlink():
                raise ToolExecutionError(
                    f"RAPP Work {field} cannot traverse a symbolic link."
                )
            if not cursor.exists():
                break
        if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
            raise ToolExecutionError(
                f"RAPP Work {field} must stay within an authorized workspace root."
            )
        return lexical

    async def _invoke(
        self,
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        plan = request.get("plan")
        plan_context = (
            self._plan_file(plan)
            if isinstance(plan, dict)
            else self._no_plan_file()
        )
        with plan_context as plan_path:
            command = (*self._resolve_command(), *self._cli_arguments(request, plan_path))
            result = await self._execute_json_command(command, deadline)
        if (
            result.get("schema") != "rapp-work-result/1"
            or result.get("profile") != "rapp-work-sdk/1"
            or result.get("protocol") != "rapp-work/1"
            or result.get("operation") != request["operation"]
            or result.get("status") == "refused"
            or result.get("refusal") is not None
            or not isinstance(result.get("result"), dict)
        ):
            raise ToolExecutionError(
                "The canonical RAPP Work CLI returned a refused or invalid result envelope."
            )
        expected_statuses = (
            {"planned"}
            if request["operation"] in RAPP_WORK_MUTATORS and not request.get("apply")
            else {"applied", "ok"}
            if request["operation"] in RAPP_WORK_MUTATORS
            else {"ok"}
        )
        if result["status"] not in expected_statuses:
            raise ToolExecutionError(
                "The canonical RAPP Work CLI returned an unexpected result status."
            )
        if request["operation"] == "status":
            return self._status_projection(result)
        return result

    @staticmethod
    def _status_projection(result: dict[str, Any]) -> dict[str, Any]:
        payload = result.get("result")
        safe_result: dict[str, Any] = {
            "authorization": "authorized",
            "ready": True,
        }
        if isinstance(payload, dict):
            sdk_version = payload.get("sdk_version")
            network = payload.get("network")
            workspace_status = payload.get("status")
            if sdk_version == RAPP_WORK_SDK_VERSION:
                safe_result["sdk_version"] = sdk_version
            if isinstance(network, bool):
                safe_result["network"] = network
            if (
                isinstance(workspace_status, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", workspace_status)
                is not None
            ):
                safe_result["workspace_status"] = workspace_status
        return {
            "operation": "status",
            "profile": result["profile"],
            "protocol": result["protocol"],
            "refusal": None,
            "result": safe_result,
            "schema": result["schema"],
            "status": "ok",
        }

    async def _ensure_compatible_version(self, deadline: float) -> None:
        if self._version_checked:
            return
        async with self._version_lock:
            if self._version_checked:
                return
            result = await self._execute_json_command(
                (*self._resolve_command(), "--version"),
                deadline,
            )
            payload = result.get("result")
            version = payload.get("sdk_version") if isinstance(payload, dict) else None
            if (
                result.get("schema") != "rapp-work-result/1"
                or result.get("operation") != "version"
                or result.get("profile") != "rapp-work-sdk/1"
                or result.get("protocol") != "rapp-work/1"
                or result.get("status") != "ok"
                or result.get("refusal") is not None
                or version != RAPP_WORK_SDK_VERSION
            ):
                raise ToolExecutionError(
                    "The configured RAPP Work CLI is incompatible; "
                    f"this plugin requires SDK {RAPP_WORK_SDK_VERSION}."
                )
            self._version_checked = True

    async def _execute_json_command(
        self,
        command: tuple[str, ...],
        deadline: float,
    ) -> dict[str, Any]:
        remaining = min(self._timeout_seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise ToolExecutionError(
                "The RAPP Work request exceeded its absolute deadline."
            )
        try:
            stdout, returncode = await asyncio.wait_for(
                self._run_process(command),
                timeout=remaining,
            )
        except TimeoutError as exc:
            raise ToolExecutionError(
                "The canonical RAPP Work CLI exceeded its execution deadline."
            ) from exc
        except _OutputLimitError as exc:
            raise ToolExecutionError(
                "The canonical RAPP Work CLI exceeded the output limit."
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(
                "The canonical RAPP Work CLI could not be started."
            ) from exc

        if returncode != 0:
            raise ToolExecutionError(
                f"The canonical RAPP Work CLI refused the operation (exit {returncode})."
            )
        if not stdout:
            raise ToolExecutionError("The canonical RAPP Work CLI returned no JSON output.")
        try:
            decoded = stdout.decode("utf-8")
            result = strict_json_loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
            raise ToolExecutionError(
                "The canonical RAPP Work CLI returned invalid or ambiguous JSON."
            ) from exc
        if not isinstance(result, dict):
            raise ToolExecutionError(
                "The canonical RAPP Work CLI must return one JSON object."
            )
        return result

    async def _run_process(
        self,
        command: tuple[str, ...],
    ) -> tuple[bytes, int]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._settings.root,
            env=self._safe_environment(),
        )
        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        total = 0

        async def read_limited(stream: asyncio.StreamReader) -> bytes:
            nonlocal total
            chunks: list[bytes] = []
            while True:
                chunk = await stream.read(65_536)
                if not chunk:
                    return b"".join(chunks)
                total += len(chunk)
                if total > self._max_output_bytes:
                    raise _OutputLimitError
                chunks.append(chunk)

        try:
            assert process.stdout is not None
            assert process.stderr is not None
            stdout_task = asyncio.create_task(read_limited(process.stdout))
            stderr_task = asyncio.create_task(read_limited(process.stderr))
            stdout, _stderr, returncode = await asyncio.gather(
                stdout_task,
                stderr_task,
                process.wait(),
            )
            return stdout, returncode
        except BaseException:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            for task in (stdout_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            if stdout_task is not None or stderr_task is not None:
                await asyncio.gather(
                    *(task for task in (stdout_task, stderr_task) if task is not None),
                    return_exceptions=True,
                )
            raise

    def _cli_arguments(
        self,
        request: dict[str, Any],
        plan_path: Path | None,
    ) -> tuple[str, ...]:
        operation = request["operation"]
        arguments = [operation]
        if operation in {"status", "verify"}:
            arguments.extend(("--root", request["root"]))
        elif operation == "discover":
            for root in request["roots"]:
                arguments.extend(("--root", root))
            if "max_entries" in request:
                arguments.extend(("--max-entries", str(request["max_entries"])))
        elif operation == "scaffold":
            arguments.extend(
                (
                    "--root",
                    request["root"],
                    "--kind",
                    request["kind"],
                    "--owner-label",
                    request["owner_label"],
                    "--slug",
                    request["slug"],
                    "--world-id",
                    request["world_id"],
                    "--mode",
                    request["mode"],
                )
            )
        elif operation == "update":
            arguments.extend(("--root", request["root"]))
        elif operation == "migrate":
            arguments.extend(
                ("--source", request["source"], "--target", request["target"])
            )
        else:
            raise ToolExecutionError(f"Unsupported RAPP Work operation: {operation}.")

        if request.get("apply"):
            if plan_path is None:
                raise ToolExecutionError("Apply requires a complete reviewed plan.")
            arguments.extend(
                (
                    "--apply",
                    "--plan",
                    str(plan_path),
                    "--plan-sha256",
                    request["plan_sha256"],
                )
            )
        return tuple(arguments)

    @contextmanager
    def _plan_file(self, plan: dict[str, Any]) -> Iterator[Path]:
        encoded = (
            json.dumps(plan, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_PLAN_BYTES:
            raise ToolExecutionError("The reviewed RAPP Work plan is too large.")
        state_path = Path(os.path.abspath(self._settings.state_path))
        self._reject_symlink_components(state_path)
        directory = state_path / "rapp-work-cli-plans"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._reject_symlink_components(directory)
        info = directory.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            raise ToolExecutionError("The RAPP Work plan staging path is unsafe.")
        path = directory / f"plan-{secrets.token_hex(16)}.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(descriptor)
            yield path
        finally:
            os.close(descriptor)
            with suppress(FileNotFoundError):
                path.unlink()

    @staticmethod
    @contextmanager
    def _no_plan_file() -> Iterator[None]:
        yield None

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        cursor = Path(path.anchor)
        for component in path.parts[1:]:
            cursor /= component
            if cursor.is_symlink():
                raise ToolExecutionError(
                    "The RAPP Work plan staging path cannot traverse a symbolic link."
                )
            if not cursor.exists():
                break

    def _resolve_command(self) -> tuple[str, ...]:
        if self._command:
            try:
                parts = tuple(shlex.split(self._command))
            except ValueError as exc:
                raise ToolExecutionError(
                    "RAPP_WORK_COMMAND is not valid shell-style text."
                ) from exc
            if not parts:
                raise ToolExecutionError("RAPP_WORK_COMMAND cannot be empty.")
        else:
            if importlib.util.find_spec("rapp_work") is None:
                raise ToolExecutionError(
                    "The canonical RAPP Work CLI is unavailable. Install it for this "
                    "interpreter or set RAPP_WORK_COMMAND; this plugin has no bundled "
                    "RAPP/1 fallback."
                )
            parts = (sys.executable, "-m", "rapp_work")

        executable = parts[0]
        if os.sep in executable or (os.altsep and os.altsep in executable):
            executable_path = Path(executable).expanduser()
            if not executable_path.is_absolute():
                executable_path = self._settings.root / executable_path
            executable_path = Path(os.path.abspath(executable_path))
            if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
                raise ToolExecutionError(
                    f"RAPP_WORK_COMMAND executable was not found: {executable}."
                )
            parts = (str(executable_path), *parts[1:])
        elif shutil.which(executable, path=os.environ.get("PATH")) is None:
            raise ToolExecutionError(
                f"RAPP_WORK_COMMAND executable was not found on PATH: {executable}."
            )
        return parts

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = {
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment.update(
            {
                "GCM_INTERACTIVE": "Never",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            }
        )
        return environment

    @staticmethod
    def _reviewed_plan(result: dict[str, Any]) -> tuple[dict[str, Any], str]:
        candidates: list[tuple[dict[str, Any], str]] = []
        containers = [result]
        for key in ("result", "data"):
            nested = result.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
        for container in containers:
            plan = container.get("plan")
            if not isinstance(plan, dict):
                continue
            digests = [
                container.get("plan_sha256"),
                container.get("plan_digest"),
                container.get("planDigest"),
            ]
            plan_digest = next(
                (value for value in digests if isinstance(value, str) and value),
                None,
            )
            if plan_digest is None:
                value = plan.get("digest")
                if isinstance(value, str) and value:
                    plan_digest = value
            if plan_digest is not None:
                candidates.append((plan, plan_digest))
        unique = {
            (json.dumps(plan, separators=(",", ":"), sort_keys=True), digest)
            for plan, digest in candidates
        }
        if len(unique) != 1:
            raise ToolExecutionError(
                "The canonical RAPP Work dry-run must return one unambiguous plan digest."
            )
        plan, digest = candidates[0]
        if _SHA256.fullmatch(digest) is None:
            raise ToolExecutionError(
                "The canonical RAPP Work dry-run returned an invalid plan SHA-256."
            )
        return plan, digest

    @classmethod
    def _reject_protected_plan(cls, plan: dict[str, Any]) -> None:
        protected = cls._protected_plan_component(plan)
        if protected is not None:
            raise ToolExecutionError(
                f"The RAPP Work plan targets protected content ({protected}) and was refused."
            )

    @classmethod
    def _protected_plan_component(
        cls,
        value: Any,
        *,
        path_context: bool = False,
    ) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_context = path_context or key.casefold() in _PATH_KEYS
                protected = cls._string_protected_component(key) if key_context else None
                if protected is not None:
                    return protected
                protected = cls._protected_plan_component(item, path_context=key_context)
                if protected is not None:
                    return protected
        elif isinstance(value, list):
            for item in value:
                protected = cls._protected_plan_component(item, path_context=path_context)
                if protected is not None:
                    return protected
        elif isinstance(value, str) and (
            path_context or "/" in value or "\\" in value
        ):
            return cls._string_protected_component(value)
        return None

    @staticmethod
    def _string_protected_component(value: str) -> str | None:
        components = [
            component.casefold()
            for component in re.split(r"[\\/]+", value.strip())
            if component not in {"", "."}
        ]
        for component in components:
            if component == ".git":
                return ".git"
            if (
                "credential" in component
                or component == ".env"
                or component.startswith(".env.")
                or component
                in {
                    ".netrc",
                    "github-app.json",
                    "id_ed25519",
                    "id_rsa",
                }
            ):
                return "credentials"
            if component == "soul.md":
                return "soul.md"
            if component == "brainstem.py":
                return "brainstem.py"
        return None
