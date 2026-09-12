from __future__ import annotations

from rapp_brainstem_gateway.auth import GitHubIdentity
from rapp_brainstem_gateway.brainstem import BrainstemService
from rapp_brainstem_gateway.config import Settings


def configure_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("RAPP_ROOT", "RAPP_AGENTS_PATH", "RAPP_SOUL_PATH", "RAPP_STATE_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_default_assets_work_outside_the_checkout(monkeypatch, tmp_path):
    configure_root(monkeypatch, tmp_path)
    settings = Settings.from_env()
    assert settings.soul_path.is_file()
    status = BrainstemService(settings).status(GitHubIdentity("42", "octocat"))
    assert status["brainstemReady"]
    assert status["loadedAgentCount"] == 4


def test_workspace_soul_takes_precedence(monkeypatch, tmp_path):
    configure_root(monkeypatch, tmp_path)
    soul = tmp_path / "soul.md"
    soul.write_text("Workspace-specific instructions.", encoding="utf-8")
    assert Settings.from_env().soul_path == soul


def test_missing_explicit_soul_is_not_silently_replaced(monkeypatch, tmp_path):
    configure_root(monkeypatch, tmp_path)
    missing = tmp_path / "not-configured.md"
    monkeypatch.setenv("RAPP_SOUL_PATH", str(missing))
    settings = Settings.from_env()
    assert settings.soul_path == missing
    assert not BrainstemService(settings).status(GitHubIdentity("42", "octocat"))["brainstemReady"]
