from __future__ import annotations

import json
import stat

from scripts.github_app_manifest_server import (
    TEAMS_OAUTH_CALLBACK,
    app_manifest,
    write_credentials,
)


def test_manifest_uses_cowork_callback():
    manifest = app_manifest("https://setup.example")

    assert manifest["redirect_url"] == "https://setup.example/callback"
    assert manifest["callback_urls"] == [TEAMS_OAUTH_CALLBACK]
    assert manifest["public"] is True
    assert manifest["default_permissions"] == {}


def test_credentials_are_written_owner_only(tmp_path):
    output = tmp_path / "github-app.json"
    write_credentials(output, {"client_id": "Iv1.test", "client_secret": "secret"})

    assert json.loads(output.read_text())["client_id"] == "Iv1.test"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
