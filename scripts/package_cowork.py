from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import shutil
import struct
import sys
import tomllib
import zlib
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cowork" / "appPackage"
PLUGIN_SOURCE = ROOT / "plugin"
RELEASE_MANIFEST = ROOT / "RAPP_WORK_PLUGIN_RELEASE.json"
CANONICAL_SDK_SOURCE_REPOSITORY = "https://github.com/kody-w/rapp-work"
CANONICAL_PLUGIN_SOURCE_REPOSITORY = (
    "https://github.com/kody-w/rapp-brainstem-plugin"
)
_RELEASE_TOP_LEVEL_FILES = (
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "hatch_build.py",
    "LICENSE",
    "PRIVACY.md",
    "README.md",
    "RAPP_WORK_SDK_PIN.json",
    "TERMS.md",
    "pyproject.toml",
    "soul.md",
)
_RELEASE_DIRECTORIES = (
    ".github",
    "agents",
    "cowork",
    "infra",
    "plugin",
    "scripts",
    "src",
    "tests",
)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_icon(path: Path, size: int, *, outline: bool) -> None:
    rows = []
    center = (size - 1) / 2
    radius = size * 0.38
    stroke = max(1, size // 16)
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
            if outline:
                alpha = 255 if radius - stroke <= distance <= radius else 0
                pixel = (255, 255, 255, alpha)
            else:
                inside = distance <= radius
                pixel = (17, 24, 39, 255) if inside else (0, 0, 0, 0)
                if inside and abs(x - center) < stroke:
                    pixel = (74, 222, 128, 255)
            row.extend(pixel)
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += png_chunk(b"IDAT", zlib.compress(raw, 9))
    png += png_chunk(b"IEND", b"")
    path.write_bytes(png)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Invalid JSON constant: {value}")
        ),
    )


def project_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def validate_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"].get("dependencies", [])
    normalized = [
        str(dependency).lower().replace("_", "-").split("[", 1)[0]
        for dependency in dependencies
    ]
    if any(value.startswith("rapp-work") for value in normalized):
        raise ValueError(
            "The canonical rapp-work SDK must be discovered, not a hard dependency"
        )


def runtime_tools() -> list[dict[str, Any]]:
    source_path = str(ROOT / "src")
    inserted = source_path not in sys.path
    if inserted:
        sys.path.insert(0, source_path)
    try:
        from rapp_brainstem_gateway.protocol import TOOLS

        return TOOLS
    finally:
        if inserted:
            sys.path.remove(source_path)


def gateway_version() -> str:
    source_path = str(ROOT / "src")
    inserted = source_path not in sys.path
    if inserted:
        sys.path.insert(0, source_path)
    try:
        from rapp_brainstem_gateway import __version__

        return __version__
    finally:
        if inserted:
            sys.path.remove(source_path)


def validate_sdk_pin(
    *,
    allow_unreleased_sdk_pin: bool = False,
) -> dict[str, Any]:
    source_path = str(ROOT / "src")
    inserted = source_path not in sys.path
    if inserted:
        sys.path.insert(0, source_path)
    try:
        from rapp_brainstem_gateway.protocol import RAPP_WORK_TOOL_OPERATIONS
        from rapp_brainstem_gateway.rapp_work import RAPP_WORK_SDK_VERSION
    finally:
        if inserted:
            sys.path.remove(source_path)

    pin = load_json(ROOT / "RAPP_WORK_SDK_PIN.json")
    expected_keys = {
        "schema",
        "distribution",
        "sdkVersion",
        "profile",
        "defaultCommand",
        "operations",
        "sourceRepository",
        "sourceCommit",
        "sourceCommitStatus",
    }
    if not isinstance(pin, dict) or set(pin) != expected_keys:
        raise ValueError("RAPP Work SDK pin must use its closed manifest shape")
    if (
        pin["schema"] != "rapp-work-sdk-consumer-pin/1"
        or pin["distribution"] != "rapp-work"
        or pin["sdkVersion"] != RAPP_WORK_SDK_VERSION
        or pin["profile"] != "rapp-work-sdk/1"
        or pin["defaultCommand"] != "python -m rapp_work"
        or pin["operations"] != list(RAPP_WORK_TOOL_OPERATIONS.values())
        or pin["sourceRepository"] != CANONICAL_SDK_SOURCE_REPOSITORY
    ):
        raise ValueError("RAPP Work SDK pin does not match the gateway contract")
    commit = pin["sourceCommit"]
    status = pin["sourceCommitStatus"]
    if (
        allow_unreleased_sdk_pin
        and os.getenv("RAPP_RELEASE_BUILD") == "1"
    ):
        raise ValueError(
            "The unreleased SDK pin development override is unavailable to release CI"
        )
    if allow_unreleased_sdk_pin and (
        commit is not None or status != "unreleased"
    ):
        raise ValueError(
            "The unreleased SDK pin development override is valid only for "
            "an explicit unreleased pin"
        )
    if commit is None:
        if (
            not allow_unreleased_sdk_pin
            or status != "unreleased"
        ):
            raise ValueError(
                "Release packaging requires the exact 40-hex canonical "
                "RAPP Work sourceCommit"
            )
    elif (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
        or status != "pinned"
    ):
        raise ValueError("RAPP Work source commit pin is invalid")
    return pin


def _release_paths() -> list[Path]:
    paths = [ROOT / relative for relative in _RELEASE_TOP_LEVEL_FILES]
    for relative in _RELEASE_DIRECTORIES:
        directory = ROOT / relative
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Release source directory is missing or unsafe: {relative}")
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    unique = sorted(set(paths), key=lambda path: path.relative_to(ROOT).as_posix())
    for path in unique:
        if not path.is_file() or path.is_symlink():
            relative = path.relative_to(ROOT).as_posix()
            raise ValueError(f"Release inventory requires regular files: {relative}")
    return unique


def build_release_manifest(
    *,
    allow_unreleased_sdk_pin: bool = False,
) -> dict[str, Any]:
    pin = validate_sdk_pin(allow_unreleased_sdk_pin=allow_unreleased_sdk_pin)
    files = []
    for path in _release_paths():
        raw = path.read_bytes()
        files.append(
            {
                "bytes": len(raw),
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    files_json = json.dumps(
        files,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "artifactPath": "RAPP_WORK_PLUGIN_RELEASE.json",
        "authority": {
            "authoritative": False,
            "protocol": "RAPP/1",
            "verificationRequired": True,
        },
        "contentSha256": hashlib.sha256(files_json).hexdigest(),
        "fileCount": len(files),
        "files": files,
        "name": "rapp-brainstem",
        "outputs": [
            {
                "kind": "cowork-zip",
                "path": "dist/rapp-brainstem-cowork.zip",
            },
            {
                "kind": "python-wheel",
                "path": (
                    f"dist/rapp_brainstem_gateway-{project_version()}-"
                    "py3-none-any.whl"
                ),
            },
            {
                "kind": "python-sdist",
                "path": f"dist/rapp_brainstem_gateway-{project_version()}.tar.gz",
            },
        ],
        "releaseStatus": (
            "final" if pin["sourceCommit"] is not None else "development-unreleased-sdk-pin"
        ),
        "schema": "rapp-work-plugin-release/1",
        "sdk": {
            "distribution": pin["distribution"],
            "operations": pin["operations"],
            "profile": pin["profile"],
            "sourceCommit": pin["sourceCommit"],
            "sourceCommitStatus": pin["sourceCommitStatus"],
            "sourceRepository": pin["sourceRepository"],
            "version": pin["sdkVersion"],
        },
        "sourceRepository": CANONICAL_PLUGIN_SOURCE_REPOSITORY,
        "totalBytes": sum(item["bytes"] for item in files),
        "version": project_version(),
    }


def release_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_release_manifest(*, allow_unreleased_sdk_pin: bool = False) -> Path:
    manifest = build_release_manifest(
        allow_unreleased_sdk_pin=allow_unreleased_sdk_pin,
    )
    RELEASE_MANIFEST.write_bytes(release_manifest_bytes(manifest))
    return RELEASE_MANIFEST


def validate_release_manifest(*, allow_unreleased_sdk_pin: bool = False) -> None:
    expected = build_release_manifest(
        allow_unreleased_sdk_pin=allow_unreleased_sdk_pin,
    )
    if not RELEASE_MANIFEST.is_file() or RELEASE_MANIFEST.is_symlink():
        raise ValueError("RAPP_WORK_PLUGIN_RELEASE.json is missing or unsafe")
    actual = RELEASE_MANIFEST.read_bytes()
    if actual != release_manifest_bytes(expected):
        raise ValueError(
            "RAPP_WORK_PLUGIN_RELEASE.json is stale; regenerate it before packaging"
        )


def _source_path(source: Path, relative: str) -> Path:
    if not relative.startswith("./"):
        raise ValueError(f"Package path must start with ./: {relative}")
    resolved_source = source.resolve()
    candidate = (source / relative[2:]).resolve()
    if candidate != resolved_source and not candidate.is_relative_to(resolved_source):
        raise ValueError(f"Package path escapes its source root: {relative}")
    return candidate


def _validate_closed_tools(tools: Any) -> None:
    if not isinstance(tools, list) or not tools:
        raise ValueError("Tool description must contain a non-empty tools list")
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"name", "description", "inputSchema"}:
            raise ValueError("Each tool description must use the closed tool shape")
        name = tool["name"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Tool names must be non-empty and unique")
        names.add(name)
        schema = tool["inputSchema"]
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or not isinstance(schema.get("properties"), dict)
        ):
            raise ValueError(f"Tool {name} must have a closed object input schema")


def validate_manifest(
    manifest: dict[str, Any],
    *,
    source: Path = SOURCE,
    plugin_source: Path = PLUGIN_SOURCE,
    allow_unreleased_sdk_pin: bool = False,
) -> None:
    required = {
        "$schema",
        "manifestVersion",
        "version",
        "id",
        "developer",
        "name",
        "description",
        "icons",
        "agentSkills",
        "agentConnectors",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError(f"Manifest missing required fields: {', '.join(missing)}")
    serialized = json.dumps(manifest)
    if "__MCP_SERVER_URL__" in serialized or "__OAUTH_REFERENCE_ID__" in serialized:
        raise ValueError("Manifest still contains unresolved placeholders")
    version = project_version()
    validate_runtime_dependencies()
    validate_sdk_pin(allow_unreleased_sdk_pin=allow_unreleased_sdk_pin)
    if gateway_version() != version:
        raise ValueError("Gateway package version must match pyproject.toml")
    if manifest["version"] != version:
        raise ValueError("Cowork manifest version must match pyproject.toml")

    plugin_manifest = load_json(plugin_source / ".claude-plugin" / "plugin.json")
    if plugin_manifest.get("version") != version:
        raise ValueError("Plugin manifest version must match pyproject.toml")

    declared_skills = manifest["agentSkills"]
    if not isinstance(declared_skills, list) or not declared_skills:
        raise ValueError("Manifest must declare at least one agent skill")
    declared_skill_names: set[str] = set()
    for declaration in declared_skills:
        if not isinstance(declaration, dict) or set(declaration) != {"folder"}:
            raise ValueError("Agent skill declarations must contain only folder")
        cowork_folder = _source_path(source, declaration["folder"])
        cowork_skill = cowork_folder / "SKILL.md"
        if not cowork_skill.is_file() or cowork_skill.is_symlink():
            raise ValueError(f"Missing regular Cowork skill: {declaration['folder']}")
        name = cowork_folder.name
        if name in declared_skill_names:
            raise ValueError(f"Duplicate Cowork skill declaration: {name}")
        declared_skill_names.add(name)
        plugin_skill = plugin_source / "skills" / name / "SKILL.md"
        if not plugin_skill.is_file() or plugin_skill.is_symlink():
            raise ValueError(f"Missing mirrored plugin skill: {name}")
        if cowork_skill.read_bytes() != plugin_skill.read_bytes():
            raise ValueError(f"Plugin and Cowork skill differ: {name}")
        if f'  version: "{version}"\n' not in cowork_skill.read_text(encoding="utf-8"):
            raise ValueError(f"Skill version must match pyproject.toml: {name}")

    source_skill_names = {
        path.name for path in (source / "skills").iterdir() if path.is_dir()
    }
    if declared_skill_names != source_skill_names:
        raise ValueError("Manifest agentSkills must declare every Cowork skill exactly once")

    connectors = manifest["agentConnectors"]
    if not isinstance(connectors, list) or len(connectors) != 1:
        raise ValueError("Manifest must declare exactly one authenticated MCP connector")
    try:
        remote = connectors[0]["toolSource"]["remoteMcpServer"]
        tool_file = remote["mcpToolDescription"]["file"]
        authorization = remote["authorization"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Manifest MCP tool description is incomplete") from exc
    if (
        not isinstance(remote.get("mcpServerUrl"), str)
        or not remote["mcpServerUrl"].startswith("https://")
        or not remote["mcpServerUrl"].endswith("/mcp")
        or not isinstance(authorization, dict)
        or authorization.get("type") != "OAuthPluginVault"
        or not isinstance(authorization.get("referenceId"), str)
        or not authorization["referenceId"]
    ):
        raise ValueError("Manifest must retain HTTPS MCP and OAuthPluginVault bearer auth")
    tool_payload = load_json(_source_path(source, tool_file))
    if not isinstance(tool_payload, dict) or set(tool_payload) != {"tools"}:
        raise ValueError("Cowork tool description must contain only tools")
    tools = tool_payload["tools"]
    _validate_closed_tools(tools)
    if tools != runtime_tools():
        raise ValueError("Cowork and gateway MCP tool schemas are not identical")


def package(
    mcp_url: str,
    oauth_reference_id: str,
    output: Path,
    *,
    allow_unreleased_sdk_pin: bool = False,
) -> Path:
    if not mcp_url.startswith("https://") or not mcp_url.endswith("/mcp"):
        raise ValueError("MCP URL must be an HTTPS URL ending in /mcp")
    if not oauth_reference_id.strip():
        raise ValueError("OAuth reference ID is required")
    validate_release_manifest(
        allow_unreleased_sdk_pin=allow_unreleased_sdk_pin,
    )

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.staging"
    archive_staging = output.parent / f".{output.name}.archive"
    if staging.exists() or archive_staging.exists():
        raise ValueError(f"Package staging path already exists for: {output}")
    staging.mkdir(mode=0o700)
    try:
        shutil.copytree(SOURCE / "skills", staging / "skills")
        shutil.copytree(SOURCE / "tools", staging / "tools")
        template = (SOURCE / "manifest.template.json").read_text(encoding="utf-8")
        manifest = json.loads(
            template.replace("__MCP_SERVER_URL__", mcp_url).replace(
                "__OAUTH_REFERENCE_ID__", oauth_reference_id
            ),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"Invalid JSON constant: {value}")
            ),
        )
        validate_manifest(
            manifest,
            allow_unreleased_sdk_pin=allow_unreleased_sdk_pin,
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        write_icon(staging / "color.png", 192, outline=False)
        write_icon(staging / "outline.png", 32, outline=True)

        with ZipFile(archive_staging, "x", ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
        archive_staging.replace(output)
    finally:
        shutil.rmtree(staging)
        archive_staging.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the RAPP Brainstem Cowork plugin")
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--oauth-reference-id", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "rapp-brainstem-cowork.zip",
    )
    parser.add_argument(
        "--allow-unreleased-sdk-pin-for-development",
        action="store_true",
        help=(
            "Permit an explicit unreleased SDK pin only for non-release "
            "development packaging"
        ),
    )
    args = parser.parse_args()
    output = package(
        args.mcp_url,
        args.oauth_reference_id,
        args.output,
        allow_unreleased_sdk_pin=args.allow_unreleased_sdk_pin_for_development,
    )
    print(output)


if __name__ == "__main__":
    main()
