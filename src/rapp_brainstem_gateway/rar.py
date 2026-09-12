from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

import agents

from .errors import ToolExecutionError
from .storage import UserStorageManager

RAR_REPOSITORY = "kody-w/RAR"
RAR_RAW_BASE = f"https://raw.githubusercontent.com/{RAR_REPOSITORY}/main"
RAR_SOURCE_BASE = f"https://github.com/{RAR_REPOSITORY}/blob/main"
DEFAULT_AGENTS_PATH = Path(agents.__file__).resolve().parent
_NAME_PATTERN = re.compile(r"^@[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def default_packages() -> list[dict[str, Any]]:
    lock = json.loads((DEFAULT_AGENTS_PATH / "defaults.lock.json").read_text(encoding="utf-8"))
    return lock["agents"]


def source_hash(source: bytes) -> str:
    # RAR's published sha256-lf-v1 contract normalizes CRLF and nothing else.
    return hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()


def verify_installed_source(directory: Path) -> tuple[Path, dict[str, Any]]:
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    path = directory / "source_agent.py"
    if (
        not isinstance(receipt, dict)
        or receipt.get("repository") != RAR_REPOSITORY
        or not isinstance(receipt.get("name"), str)
        or not _NAME_PATTERN.fullmatch(receipt["name"])
        or not isinstance(receipt.get("sha256"), str)
        or not _SHA256_PATTERN.fullmatch(receipt["sha256"])
    ):
        raise ToolExecutionError("The installed RAR receipt is invalid.")
    if source_hash(path.read_bytes()) != receipt.get("sha256"):
        raise ToolExecutionError("Installed RAR source changed; refusing to load it.")
    return path, receipt


class RARClient:
    """A bounded hosted client for RAR's public static catalog and source-hash contract."""

    def __init__(self, storage: UserStorageManager, *, client: httpx.Client | None = None) -> None:
        self.storage = storage
        self.install_path = storage.user_path / "agents"
        self._client = client

    def _fetch(self, relative_path: str, *, maximum_bytes: int) -> bytes:
        parts = PurePosixPath(relative_path).parts
        if (
            not parts
            or relative_path.startswith("/")
            or any(part in {".", ".."} for part in parts)
            or "\\" in relative_path
            or "?" in relative_path
            or "#" in relative_path
            or "%" in relative_path
        ):
            raise ToolExecutionError("RAR returned an invalid source path.")
        client = self._client or httpx.Client(timeout=8, follow_redirects=False, trust_env=False)
        try:
            with client.stream(
                "GET", f"{RAR_RAW_BASE}/{relative_path}", headers={"User-Agent": "RAPP-Brainstem"}
            ) as response:
                response.raise_for_status()
                source = bytearray()
                for chunk in response.iter_bytes():
                    source.extend(chunk)
                    if len(source) > maximum_bytes:
                        raise ToolExecutionError("RAR response exceeds the supported size limit.")
                return bytes(source)
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                "RAR could not be reached or rejected the download. "
                "Nothing unverified was installed."
            ) from exc
        finally:
            if self._client is None:
                client.close()

    def _catalog(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        cached = None if refresh else self.storage.read_file("rar", "catalog.json")
        if cached is not None:
            try:
                cache = json.loads(cached)
            except json.JSONDecodeError as exc:
                raise ToolExecutionError("The cached RAR catalog is invalid.") from exc
            if (
                not isinstance(cache, dict)
                or not isinstance(cache.get("fetched_at"), int | float)
                or not isinstance(cache.get("agents"), list)
            ):
                raise ToolExecutionError("The cached RAR catalog is invalid.")
            if 0 <= time.time() - cache["fetched_at"] < 300:
                return cache["agents"]
        try:
            catalog = json.loads(self._fetch("registry.json", maximum_bytes=16 * 1024 * 1024))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolExecutionError("RAR returned an invalid catalog.") from exc
        entries = catalog.get("agents") if isinstance(catalog, dict) else None
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("name"), str) for entry in entries
        ):
            raise ToolExecutionError("RAR returned an invalid agent list.")
        self.storage.write_file(
            "rar", "catalog.json", json.dumps({"fetched_at": time.time(), "agents": entries})
        )
        return entries

    @staticmethod
    def _summary(entry: dict[str, Any]) -> dict[str, Any]:
        fields = ("name", "version", "display_name", "description", "category", "quality_tier")
        result = {field: entry.get(field) for field in fields}
        result.update(
            sha256=entry.get("_sha256"),
            dependencies=entry.get("dependencies", []),
            requires_env=entry.get("requires_env", []),
            source=f"{RAR_SOURCE_BASE}/{entry.get('_file', '')}",
        )
        return result

    def perform(self, **kwargs: Any) -> dict[str, Any]:
        action = kwargs.get("action")
        if action == "list_installed":
            return self.list_installed()
        if action not in {"discover", "search", "get_info", "install"}:
            raise ToolExecutionError("Unknown RAR action.")
        name = kwargs.get("agent_name", "")
        if action in {"get_info", "install"} and (
            not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name)
        ):
            raise ToolExecutionError("RAR requires the exact @publisher/agent_name.")
        if action == "install" and kwargs.get("confirm") is not True:
            raise ToolExecutionError(
                "Ask the user to confirm installing this trusted Python agent."
            )
        if action == "install" and name in {item["name"] for item in default_packages()}:
            return {"status": "success", "agent": name, "alreadyInstalled": True, "bundled": True}
        query = kwargs.get("query", "")
        if action == "search" and (not isinstance(query, str) or not query.strip()):
            raise ToolExecutionError("RAR search requires a non-empty query.")
        entries = self._catalog(refresh=action == "install")
        if action in {"get_info", "install"}:
            entry = next((item for item in entries if item["name"] == name), None)
            if entry is None:
                raise ToolExecutionError("That agent is not in RAR. Search for its exact name.")
            if action == "install":
                return self._install(entry)
            return {"status": "success", "agent": self._summary(entry)}

        category, tier = kwargs.get("category"), kwargs.get("tier")
        terms = query.lower().split() if action == "search" else []
        matches = []
        for entry in entries:
            if category and entry.get("category") != category:
                continue
            if tier and entry.get("quality_tier") != tier:
                continue
            searchable = " ".join(
                str(entry.get(field, ""))
                for field in ("name", "display_name", "description", "tags")
            ).lower()
            if all(term in searchable for term in terms):
                matches.append(entry)
        tier_order = {"official": 0, "verified": 1, "community": 2, "experimental": 3}
        matches.sort(key=lambda item: (tier_order.get(item.get("quality_tier"), 4), item["name"]))
        limit = kwargs.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            raise ToolExecutionError("RAR result limit must be an integer from 1 to 30.")
        return {
            "status": "success",
            "total": len(matches),
            "agents": [self._summary(entry) for entry in matches[:limit]],
        }

    def _install(self, entry: dict[str, Any]) -> dict[str, Any]:
        name = entry["name"]
        if entry.get("type") == "stub":
            raise ToolExecutionError("Private RAR stubs are not supported by this public client.")
        dependencies = entry.get("dependencies", [])
        if not isinstance(dependencies, list) or any(
            dependency != "@rapp/basic_agent" for dependency in dependencies
        ):
            raise ToolExecutionError(
                "This agent needs other RAR packages. This gateway installs standalone "
                "BasicAgent packages only; dependent bundles require operator setup."
            )
        expected = entry.get("_sha256")
        relative_path = entry.get("_file")
        if not isinstance(expected, str) or not _SHA256_PATTERN.fullmatch(expected):
            raise ToolExecutionError(
                "RAR did not publish a valid source hash; installation refused."
            )
        if (
            not isinstance(relative_path, str)
            or not relative_path.startswith("agents/")
            or not relative_path.endswith(".py")
        ):
            raise ToolExecutionError("RAR did not publish a Python agent source path.")
        source = self._fetch(relative_path, maximum_bytes=2 * 1024 * 1024)
        if source_hash(source) != expected:
            raise ToolExecutionError("RAR source hash mismatch; nothing was installed.")
        try:
            tree = ast.parse(source.decode("utf-8"))
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise ToolExecutionError(
                "RAR source is not valid Python; nothing was installed."
            ) from exc
        manifests = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__manifest__"
                for target in node.targets
            )
        ]
        try:
            manifest = ast.literal_eval(manifests[0]) if len(manifests) == 1 else None
        except (ValueError, TypeError) as exc:
            raise ToolExecutionError("RAR source has an invalid manifest.") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != name
            or manifest.get("version") != entry.get("version")
        ):
            raise ToolExecutionError("RAR source manifest does not match its catalog entry.")
        if not any(
            isinstance(node, ast.ClassDef)
            and any(
                (isinstance(base, ast.Name) and base.id == "BasicAgent")
                or (isinstance(base, ast.Attribute) and base.attr == "BasicAgent")
                for base in node.bases
            )
            and any(
                isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                and method.name == "perform"
                for method in node.body
            )
            for node in tree.body
        ):
            raise ToolExecutionError("RAR source must define a BasicAgent with a perform method.")
        package_id = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
        destination = self.install_path / package_id
        receipt = {
            "repository": RAR_REPOSITORY,
            "name": name,
            "version": entry.get("version"),
            "sha256": expected,
            "source": f"{RAR_RAW_BASE}/{relative_path}",
            "requires_env": entry.get("requires_env", []),
        }
        self.install_path.mkdir(parents=True, exist_ok=True)
        # A directory rename admits source and receipt together; the loader ignores staging dirs.
        with tempfile.TemporaryDirectory(prefix=".install-", dir=self.install_path) as temporary:
            staging = Path(temporary) / "package"
            staging.mkdir()
            (staging / "source_agent.py").write_bytes(source)
            with self.storage.transaction():
                if destination.exists():
                    _, existing = verify_installed_source(destination)
                    if existing["sha256"] != expected:
                        raise ToolExecutionError(
                            "Another version is installed. "
                            "Operator removal is required before replacing it."
                        )
                    return {"status": "success", "agent": name, "alreadyInstalled": True}
            self._validate_install(staging / "source_agent.py")
            (staging / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            with self.storage.transaction():
                if destination.exists():
                    _, existing = verify_installed_source(destination)
                    if existing["sha256"] != expected:
                        raise ToolExecutionError("A different version was installed concurrently.")
                    return {"status": "success", "agent": name, "alreadyInstalled": True}
                staging.rename(destination)
        return {
            "status": "success",
            "agent": name,
            "sha256": expected,
            "requires_env": receipt["requires_env"],
            "message": (
                "Installed for this user. It will load on the next Brainstem request. "
                "brainstem_status lists its capabilities and any later load errors."
            ),
        }

    def _validate_install(self, source_path: Path) -> None:
        from .agent_context import AgentContext, bind_agent_context
        from .agents import AgentRegistry

        context = AgentContext(self.storage)
        with bind_agent_context(context):
            try:
                candidates = AgentRegistry._load_file(source_path, context)
            except ModuleNotFoundError as exc:
                raise ToolExecutionError(
                    f"Agent needs Python dependency {exc.name!r}; nothing was installed. "
                    "The gateway operator must provide dependencies, not runtime pip installs."
                ) from exc
        current = AgentRegistry(
            DEFAULT_AGENTS_PATH,
            state_path=self.storage.user_path.parent,
            include_defaults=True,
        ).catalog(user_id=self.storage.current_guid)
        names = [agent.name for agent in candidates]
        if len(set(names)) != len(names) or {agent.name for agent in current.agents}.intersection(
            names
        ):
            raise ToolExecutionError("Agent tool names conflict with an installed capability.")

    def list_installed(self) -> dict[str, Any]:
        installed = [
            {"name": item["name"], "version": item["version"], "bundled": True}
            for item in default_packages()
        ]
        if self.install_path.exists():
            for directory in sorted(self.install_path.iterdir()):
                if directory.is_dir() and not directory.name.startswith("."):
                    _, receipt = verify_installed_source(directory)
                    installed.append(
                        {"name": receipt["name"], "version": receipt["version"], "bundled": False}
                    )
        return {"status": "success", "agents": installed}
