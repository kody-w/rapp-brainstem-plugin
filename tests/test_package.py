from __future__ import annotations

import json
from zipfile import ZipFile

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

    connector = manifest["agentConnectors"][0]["toolSource"]["remoteMcpServer"]
    assert connector["mcpServerUrl"] == "https://brainstem.example/mcp"
    assert connector["authorization"]["referenceId"] == "oauth-config-id"
