from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from rapp_brainstem_gateway.protocol import TOOLS
from scripts.package_cowork import package


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
        assert "tools/brainstem-tools.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert json.loads(archive.read("tools/brainstem-tools.json"))["tools"] == TOOLS
        skill = archive.read("skills/brainstem/SKILL.md").decode()
        for name in ("ContextMemory", "ManageMemory", "HackerNews", "RARRemoteAgent"):
            assert name in skill

    connector = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert connector["mcpServerUrl"] == "https://brainstem.example/mcp"
    assert connector["authorization"]["referenceId"] == "oauth-config-id"


def test_portable_and_cowork_skill_instructions_match():
    root = Path(__file__).resolve().parents[1]
    portable = (root / "plugin/skills/brainstem/SKILL.md").read_text(encoding="utf-8")
    cowork = (root / "cowork/appPackage/skills/brainstem/SKILL.md").read_text(encoding="utf-8")
    assert portable == cowork


def test_docker_provides_bundled_assets_before_building_the_wheel():
    root = Path(__file__).resolve().parents[1]
    copied = set()
    for line in (root / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("RUN pip install"):
            break
        if line.startswith("COPY "):
            copied.update(line.split()[1:-1])
    assert {"agents", "soul.md"} <= copied
