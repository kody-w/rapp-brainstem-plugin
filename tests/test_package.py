from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from rapp_brainstem_gateway.protocol import TOOLS
from scripts import package_cowork as package_module
from scripts.package_cowork import (
    PLUGIN_SOURCE,
    RELEASE_MANIFEST,
    ROOT,
    SOURCE,
    build_release_manifest,
    package,
    validate_manifest,
    validate_release_manifest,
    validate_runtime_dependencies,
    validate_sdk_pin,
)

ACCEPTED_SDK_COMMIT = "29ead23b21645f8d7682ee00414930ffa9ce0ca6"


def test_builds_uploadable_cowork_package(tmp_path):
    output = tmp_path / "plugin.zip"
    package(
        "https://brainstem.example/mcp",
        "oauth-config-id",
        output,
    )

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "color.png" in names
        assert "outline.png" in names
        assert "skills/brainstem/SKILL.md" in names
        assert "skills/rapp-work/SKILL.md" in names
        assert "tools/brainstem-tools.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        tools = json.loads(archive.read("tools/brainstem-tools.json"))["tools"]

    assert names == {
        "color.png",
        "manifest.json",
        "outline.png",
        "skills/brainstem/SKILL.md",
        "skills/rapp-work/SKILL.md",
        "tools/brainstem-tools.json",
    }
    assert not (tmp_path / ".plugin.zip.staging").exists()
    assert not (tmp_path / ".plugin.zip.archive").exists()
    connector = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert connector["mcpServerUrl"] == "https://brainstem.example/mcp"
    assert connector["authorization"]["referenceId"] == "oauth-config-id"
    assert manifest["version"] == "0.2.1"
    assert {skill["folder"] for skill in manifest["agentSkills"]} == {
        "./skills/brainstem",
        "./skills/rapp-work",
    }
    assert tools == TOOLS


def resolved_manifest() -> dict:
    template = (SOURCE / "manifest.template.json").read_text(encoding="utf-8")
    return json.loads(
        template.replace("__MCP_SERVER_URL__", "https://brainstem.example/mcp").replace(
            "__OAUTH_REFERENCE_ID__", "oauth-config-id"
        )
    )


def test_plugin_and_cowork_skills_are_byte_identical():
    for name in ("brainstem", "rapp-work"):
        plugin_skill = PLUGIN_SOURCE / "skills" / name / "SKILL.md"
        cowork_skill = SOURCE / "skills" / name / "SKILL.md"
        assert plugin_skill.read_bytes() == cowork_skill.read_bytes()


def test_package_validation_refuses_tool_schema_drift(tmp_path: Path):
    source = tmp_path / "appPackage"
    shutil.copytree(SOURCE, source)
    tool_file = source / "tools" / "brainstem-tools.json"
    payload = json.loads(tool_file.read_text(encoding="utf-8"))
    payload["tools"][0]["inputSchema"]["additionalProperties"] = True
    tool_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="closed object input schema"):
        validate_manifest(
            resolved_manifest(),
            source=source,
        )


def test_package_validation_refuses_version_drift():
    manifest = resolved_manifest()
    manifest["version"] = "9.9.9"

    with pytest.raises(ValueError, match="must match pyproject"):
        validate_manifest(manifest)


def test_rapp_work_sdk_pin_is_final(monkeypatch):
    monkeypatch.delenv("RAPP_RELEASE_BUILD", raising=False)
    validate_runtime_dependencies()
    pin = validate_sdk_pin()

    assert pin["sdkVersion"] == "1.0.0"
    assert pin["sourceRepository"] == "https://github.com/kody-w/rapp-work"
    assert pin["sourceCommit"] == ACCEPTED_SDK_COMMIT
    assert pin["sourceCommitStatus"] == "pinned"
    with pytest.raises(ValueError, match="only for an explicit unreleased pin"):
        validate_sdk_pin(allow_unreleased_sdk_pin=True)


def test_unreleased_sdk_pin_requires_explicit_development_override(monkeypatch):
    monkeypatch.delenv("RAPP_RELEASE_BUILD", raising=False)
    pin = json.loads((ROOT / "RAPP_WORK_SDK_PIN.json").read_text(encoding="utf-8"))
    pin["sourceCommit"] = None
    pin["sourceCommitStatus"] = "unreleased"
    monkeypatch.setattr(package_module, "load_json", lambda _: pin)

    with pytest.raises(ValueError, match="exact 40-hex"):
        validate_sdk_pin()
    assert validate_sdk_pin(allow_unreleased_sdk_pin=True) == pin
    monkeypatch.setenv("RAPP_RELEASE_BUILD", "1")
    with pytest.raises(ValueError, match="unavailable to release CI"):
        validate_sdk_pin(allow_unreleased_sdk_pin=True)


def test_sdk_pin_requires_exact_canonical_repository(monkeypatch):
    pin = json.loads((ROOT / "RAPP_WORK_SDK_PIN.json").read_text(encoding="utf-8"))
    pin["sourceRepository"] = "https://github.com/example/rapp-work"
    monkeypatch.setattr(package_module, "load_json", lambda _: pin)

    with pytest.raises(ValueError, match="gateway contract"):
        validate_sdk_pin()


def test_release_manifest_is_deterministic_and_current():
    validate_release_manifest()
    actual = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    expected = build_release_manifest()

    assert actual == expected
    assert actual["schema"] == "rapp-work-plugin-release/1"
    assert actual["releaseStatus"] == "final"
    assert actual["sourceRepository"] == (
        "https://github.com/kody-w/rapp-brainstem-plugin"
    )
    assert actual["sdk"]["sourceRepository"] == "https://github.com/kody-w/rapp-work"
    assert actual["sdk"]["sourceCommit"] == ACCEPTED_SDK_COMMIT
    assert actual["sdk"]["sourceCommitStatus"] == "pinned"
    assert len(actual["contentSha256"]) == 64
    assert actual["fileCount"] == len(actual["files"])
    assert actual["totalBytes"] == sum(item["bytes"] for item in actual["files"])
    assert {
        ".github/workflows/ci.yml",
        "Dockerfile",
        "infra/deploy.sh",
        "RAPP_WORK_SDK_PIN.json",
    } <= {item["path"] for item in actual["files"]}


def test_production_image_installs_only_the_exact_canonical_sdk_commit():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG RAPP_WORK_SOURCE_COMMIT" in dockerfile
    assert "https://github.com/kody-w/rapp-work.git" in dockerfile
    assert 'pin.get("sourceCommit")==commit' in dockerfile
    assert (
        '"rapp-work @ git+https://github.com/kody-w/rapp-work.git@'
        '${RAPP_WORK_SOURCE_COMMIT}"'
    ) in dockerfile
    assert 'read_text("direct_url.json")' in dockerfile
    assert 'vcs.get("commit_id")==commit' in dockerfile
    assert 'RAPP_WORK_COMMAND="/opt/rapp-work/bin/python -m rapp_work"' in dockerfile
    assert "RAPP_WORK_ROOTS=/workspaces" in dockerfile
    assert "pip install rapp-work" not in dockerfile


def test_deployment_requires_pin_and_immutable_owner_authorization():
    deploy = (ROOT / "infra" / "deploy.sh").read_text(encoding="utf-8")

    assert "--rapp-work-owner-id" in deploy
    assert "^[0-9a-f]{40}$" in deploy
    assert 'SDK_REPOSITORY="https://github.com/kody-w/rapp-work"' in deploy
    assert 'SDK_COMMAND="/opt/rapp-work/bin/python -m rapp_work"' in deploy
    assert 'SDK_ROOTS="/workspaces"' in deploy
    assert 'SDK_COMMIT="$PIN_COMMIT"' in deploy
    assert '"RAPP_WORK_SOURCE_COMMIT=${SDK_COMMIT}"' in deploy
    assert "RAPP_WORK_OWNER_IDS" in deploy
    assert '"RAPP_WORK_ROOTS": {' in deploy
    assert '"value": sdk_roots' in deploy
    assert '"type": "Readiness"' in deploy
    assert "mktemp" not in deploy


def test_deployment_fails_closed_before_azure_on_commit_mismatch():
    result = subprocess.run(
        [
            str(ROOT / "infra" / "deploy.sh"),
            "--subscription",
            "unused",
            "--rapp-work-source-commit",
            "0" * 40,
            "--rapp-work-owner-id",
            "42",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert (
        result.stderr.strip()
        == "--rapp-work-source-commit must equal RAPP_WORK_SDK_PIN.json"
    )


def test_ci_uses_final_pin_without_development_override():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    release_job = workflow.split("\n  release:\n", 1)[1]

    assert "--allow-unreleased-sdk-pin-for-development" not in workflow
    assert "RAPP_WORK_ALLOW_UNRELEASED_SDK_PIN_FOR_DEVELOPMENT" not in workflow
    assert 'RAPP_RELEASE_BUILD: "1"' in release_job
    assert "docker build" in release_job


def test_python_build_hook_enforces_the_same_release_pin_gate():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    hook = (ROOT / "hatch_build.py").read_text(encoding="utf-8")

    assert '[tool.hatch.build.hooks.custom]' in pyproject
    assert 'path = "hatch_build.py"' in pyproject
    assert "validate_release_manifest" in hook
    assert "RAPP_WORK_ALLOW_UNRELEASED_SDK_PIN_FOR_DEVELOPMENT" in hook
