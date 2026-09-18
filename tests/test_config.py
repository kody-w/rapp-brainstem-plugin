from __future__ import annotations

import os

import pytest

from rapp_brainstem_gateway.config import Settings


def test_rapp_work_configuration_from_environment(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("RAPP_ROOT", str(tmp_path))
    monkeypatch.setenv("RAPP_WORK_COMMAND", "/opt/rapp-work/bin/python -m rapp_work")
    monkeypatch.setenv(
        "RAPP_WORK_ROOTS",
        f" {first} {os.pathsep}{second}",
    )
    monkeypatch.setenv("RAPP_WORK_TIMEOUT_SECONDS", "500")
    monkeypatch.setenv("RAPP_WORK_MAX_OUTPUT_BYTES", "999999999")
    monkeypatch.setenv("RAPP_WORK_OWNER_IDS", "42,1001")
    monkeypatch.setenv(
        "RAPP_WORK_PRINCIPAL_ROOTS",
        f'{{"1002":["{second}"]}}',
    )

    settings = Settings.from_env()

    assert settings.rapp_work_command == "/opt/rapp-work/bin/python -m rapp_work"
    assert settings.rapp_work_roots == (first.resolve(), second.resolve())
    assert settings.rapp_work_owner_ids == frozenset({"42", "1001"})
    assert settings.rapp_work_principal_roots == (("1002", (second.resolve(),)),)
    assert settings.rapp_work_timeout_seconds == 20
    assert settings.rapp_work_max_output_bytes == 4_194_304


def test_rapp_work_defaults_to_gateway_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPP_ROOT", str(tmp_path))
    monkeypatch.delenv("RAPP_WORK_COMMAND", raising=False)
    monkeypatch.delenv("RAPP_WORK_ROOTS", raising=False)
    monkeypatch.delenv("RAPP_WORK_OWNER_IDS", raising=False)
    monkeypatch.delenv("RAPP_WORK_PRINCIPAL_ROOTS", raising=False)

    settings = Settings.from_env()

    assert settings.rapp_work_command is None
    assert settings.rapp_work_roots == (tmp_path.resolve(),)
    assert settings.rapp_work_owner_ids == frozenset()
    assert settings.rapp_work_principal_roots == ()


def test_rapp_work_roots_must_be_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPP_ROOT", str(tmp_path))
    monkeypatch.setenv("RAPP_WORK_ROOTS", "relative")

    with pytest.raises(ValueError, match="absolute"):
        Settings.from_env()


@pytest.mark.parametrize("principal", ["octocat", "0", "-1", "1.0"])
def test_rapp_work_owner_ids_must_be_immutable_numeric_ids(
    tmp_path,
    monkeypatch,
    principal,
):
    monkeypatch.setenv("RAPP_ROOT", str(tmp_path))
    monkeypatch.setenv("RAPP_WORK_OWNER_IDS", principal)

    with pytest.raises(ValueError, match="numeric GitHub user IDs"):
        Settings.from_env()


def test_principal_root_mapping_must_stay_inside_global_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("RAPP_ROOT", str(tmp_path))
    monkeypatch.setenv("RAPP_WORK_ROOTS", str(allowed))
    monkeypatch.setenv(
        "RAPP_WORK_PRINCIPAL_ROOTS",
        f'{{"42":["{outside}"]}}',
    )

    with pytest.raises(ValueError, match="must stay within RAPP_WORK_ROOTS"):
        Settings.from_env()
