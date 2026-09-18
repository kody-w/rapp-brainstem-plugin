from __future__ import annotations

import asyncio
import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from rapp_brainstem_gateway.errors import ToolExecutionError
from rapp_brainstem_gateway.rapp_work import RappWorkService

FAKE_SDK = Path(__file__).with_name("fake_rapp_work_sdk.py")


def service(settings, capture: Path, mode: str = "normal") -> RappWorkService:
    command = shlex.join([sys.executable, str(FAKE_SDK), mode, str(capture)])
    return RappWorkService(settings, command=command, roots=(settings.root,))


def captured(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "operation, arguments, expected",
    [
        ("status", {}, {"operation": "status", "root": "."}),
        ("verify", {"root": "."}, {"operation": "verify", "root": "."}),
        (
            "discover",
            {"roots": ["."], "maxEntries": 25},
            {"max_entries": 25, "operation": "discover", "roots": ["."]},
        ),
        (
            "scaffold",
            {
                "root": "new",
                "kind": "workspace",
                "ownerLabel": "rapp",
                "slug": "new",
                "worldId": "local",
                "mode": "solo",
            },
            {
                "apply": False,
                "kind": "workspace",
                "mode": "solo",
                "operation": "scaffold",
                "owner_label": "rapp",
                "root": "new",
                "slug": "new",
                "world_id": "local",
            },
        ),
        (
            "update",
            {"root": "."},
            {"apply": False, "operation": "update", "root": "."},
        ),
        (
            "migrate",
            {"source": ".", "target": "successor"},
            {
                "apply": False,
                "operation": "migrate",
                "source": ".",
                "target": "successor",
            },
        ),
    ],
)
async def test_all_six_operations_use_the_canonical_cli_contract(
    settings,
    operation,
    arguments,
    expected,
):
    capture = settings.root / f"{operation}.jsonl"

    result = await service(settings, capture).execute(
        operation,
        arguments,
        principal="42",
    )

    normalized = {
        key: (
            [str(settings.root / item) for item in value]
            if key == "roots"
            else str(settings.root / value)
            if key in {"root", "source", "target"}
            else value
        )
        for key, value in expected.items()
    }
    assert captured(capture) == [normalized]
    assert result["operation"] == operation


async def test_invokes_configured_sdk_with_offline_default(settings, monkeypatch):
    capture = settings.root / "requests.jsonl"
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-inherit")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "do-not-inherit")

    result = await service(settings, capture).execute(
        "verify",
        {"root": "."},
        principal="42",
    )

    request = captured(capture)[0]
    assert request == {"operation": "verify", "root": str(settings.root)}
    assert result["result"]["credential_environment"] == []


async def test_discovery_normalizes_every_allowlisted_root(settings):
    first = settings.root / "first"
    second = settings.root / "second"
    first.mkdir()
    second.mkdir()
    capture = settings.root / "requests.jsonl"

    await service(settings, capture).execute(
        "discover",
        {"roots": ["first", second], "maxEntries": 50},
        principal="42",
    )

    assert captured(capture)[0] == {
        "max_entries": 50,
        "operation": "discover",
        "roots": [str(first), str(second)],
    }


async def test_online_execution_is_refused_before_sdk_launch(settings):
    capture = settings.root / "requests.jsonl"

    with pytest.raises(ToolExecutionError, match="only the canonical offline path"):
        await service(settings, capture).execute(
            "status",
            {"offline": False},
            principal="42",
        )

    assert captured(capture) == []


async def test_authentication_alone_does_not_authorize_shared_roots(settings):
    capture = settings.root / "requests.jsonl"

    with pytest.raises(ToolExecutionError, match="not authorized"):
        await service(settings, capture).execute(
            "status",
            {},
            principal="43",
        )

    assert captured(capture) == []


async def test_principal_root_mapping_isolates_authorized_roots(settings):
    first = settings.root / "first"
    second = settings.root / "second"
    first.mkdir()
    second.mkdir()
    capture = settings.root / "requests.jsonl"
    mapped = replace(
        settings,
        rapp_work_owner_ids=frozenset(),
        rapp_work_principal_roots=(
            ("1001", (first,)),
            ("1002", (second,)),
        ),
    )
    runner = service(mapped, capture)

    await runner.execute("verify", {"root": first}, principal="1001")
    with pytest.raises(ToolExecutionError, match="authorized workspace root"):
        await runner.execute("verify", {"root": second}, principal="1001")

    assert captured(capture) == [{"operation": "verify", "root": str(first)}]


async def test_status_reports_readiness_without_disclosing_roots(settings):
    capture = settings.root / "requests.jsonl"

    result = await service(settings, capture).execute(
        "status",
        {},
        principal="42",
    )

    serialized = json.dumps(result, sort_keys=True)
    assert result["result"]["authorization"] == "authorized"
    assert result["result"]["ready"] is True
    assert str(settings.root) not in serialized
    assert '"root"' not in serialized


async def test_readiness_is_fail_closed_and_does_not_disclose_configuration(settings):
    capture = settings.root / "requests.jsonl"
    ready = await service(settings, capture).readiness()
    unconfigured = await service(
        replace(settings, rapp_work_owner_ids=frozenset()),
        capture,
    ).readiness()

    assert ready == {
        "authorizationConfigured": True,
        "ready": True,
        "sdkReady": True,
        "workspaceRootsReady": True,
    }
    assert unconfigured["authorizationConfigured"] is False
    assert unconfigured["ready"] is False
    assert str(settings.root) not in json.dumps(ready)
    assert str(settings.root) not in json.dumps(unconfigured)


async def test_mutators_plan_before_apply_and_forward_exact_plan(settings):
    capture = settings.root / "requests.jsonl"
    runner = service(settings, capture)
    arguments = {"root": "new-workspace"}

    planned = await runner.execute("update", arguments, principal="42")
    digest = planned["result"]["plan_sha256"]
    applied = await runner.execute(
        "update",
        {**arguments, "apply": True, "planDigest": digest},
        principal="42",
    )

    requests = captured(capture)
    assert [request["apply"] for request in requests] == [False, True]
    assert requests[-1]["plan"] == planned["result"]["plan"]
    assert requests[-1]["plan_sha256"] == digest
    assert applied["result"]["status"] == "applied"

    with pytest.raises(ToolExecutionError, match="No current reviewed plan"):
        await runner.execute(
            "update",
            {**arguments, "apply": True, "planDigest": digest},
            principal="42",
        )
    assert [request["apply"] for request in captured(capture)] == [False, True]


async def test_changed_plan_digest_is_refused_before_apply(settings):
    capture = settings.root / "requests.jsonl"
    runner = service(settings, capture)
    await runner.execute("update", {"root": "workspace"}, principal="42")

    with pytest.raises(ToolExecutionError, match="No current reviewed plan"):
        await runner.execute(
            "update",
            {
                "root": "workspace",
                "apply": True,
                "planDigest": "0" * 64,
            },
            principal="42",
        )

    assert [request["apply"] for request in captured(capture)] == [False]


async def test_reviewed_plan_is_bound_to_principal_and_inputs(settings):
    capture = settings.root / "requests.jsonl"
    runner = service(settings, capture)
    planned = await runner.execute(
        "update",
        {"root": "workspace"},
        principal="1001",
    )
    digest = planned["result"]["plan_sha256"]

    with pytest.raises(ToolExecutionError, match="No current reviewed plan"):
        await runner.execute(
            "update",
            {"root": "workspace", "apply": True, "planDigest": digest},
            principal="1002",
        )
    with pytest.raises(ToolExecutionError, match="inputs do not match"):
        await runner.execute(
            "update",
            {"root": "different", "apply": True, "planDigest": digest},
            principal="1001",
        )

    assert [request["apply"] for request in captured(capture)] == [False]


@pytest.mark.parametrize(
    "mode, protected",
    [
        ("unsafe-git", ".git"),
        ("unsafe-credentials", "credentials"),
        ("unsafe-soul", "soul.md"),
        ("unsafe-brainstem", "brainstem.py"),
        ("unsafe-env", "credentials"),
    ],
)
async def test_protected_plans_are_refused(settings, mode, protected):
    capture = settings.root / f"{mode}.jsonl"

    with pytest.raises(ToolExecutionError, match=protected):
        await service(settings, capture, mode).execute(
            "update",
            {"root": "workspace"},
            principal="42",
        )

    assert [request["apply"] for request in captured(capture)] == [False]


async def test_refuses_paths_outside_roots_and_symlink_escape(settings):
    capture = settings.root / "requests.jsonl"
    outside = settings.root.parent / f"{settings.root.name}-outside"
    outside.mkdir()
    link = settings.root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    runner = service(settings, capture)

    with pytest.raises(ToolExecutionError, match="authorized workspace root"):
        await runner.execute("verify", {"root": outside}, principal="42")
    with pytest.raises(ToolExecutionError, match="symbolic link"):
        await runner.execute("verify", {"root": link}, principal="42")

    assert captured(capture) == []


async def test_apply_plan_file_cannot_escape_through_state_symlink(settings):
    capture = settings.root / "requests.jsonl"
    actual_state = settings.root / "actual-state"
    actual_state.mkdir()
    linked_state = settings.root / "linked-state"
    linked_state.symlink_to(actual_state, target_is_directory=True)
    runner = service(replace(settings, state_path=linked_state), capture)
    planned = await runner.execute("update", {"root": "workspace"}, principal="42")

    with pytest.raises(ToolExecutionError, match="cannot traverse a symbolic link"):
        await runner.execute(
            "update",
            {
                "root": "workspace",
                "apply": True,
                "planDigest": planned["result"]["plan_sha256"],
            },
            principal="42",
        )

    assert [request["apply"] for request in captured(capture)] == [False]


@pytest.mark.parametrize(
    "mode, message",
    [
        ("duplicate", "invalid or ambiguous JSON"),
        ("invalid", "invalid or ambiguous JSON"),
        ("nonzero", "exit 7"),
        ("refused-zero", "refused or invalid result envelope"),
    ],
)
async def test_refuses_bad_sdk_results(settings, mode, message):
    capture = settings.root / f"{mode}.jsonl"

    with pytest.raises(ToolExecutionError, match=message):
        await service(settings, capture, mode).execute("status", {}, principal="42")


async def test_bounds_sdk_output_and_runtime(settings):
    capture = settings.root / "requests.jsonl"
    output_bounded = replace(
        settings,
        rapp_work_max_output_bytes=1024,
        rapp_work_timeout_seconds=1,
    )

    with pytest.raises(ToolExecutionError, match="output limit"):
        await service(output_bounded, capture, "oversize").execute(
            "status",
            {},
            principal="42",
        )

    timeout_bounded = replace(
        settings,
        rapp_work_timeout_seconds=0.05,
    )
    with pytest.raises(ToolExecutionError, match="execution deadline"):
        await service(timeout_bounded, capture, "timeout").execute(
            "status",
            {},
            principal="42",
        )


async def test_version_and_operation_share_one_absolute_deadline(
    settings,
    monkeypatch,
):
    capture = settings.root / "requests.jsonl"
    bounded = replace(
        settings,
        request_timeout_seconds=0.4,
        rapp_work_timeout_seconds=0.3,
    )
    runner = service(bounded, capture)
    calls = []

    async def delayed_process(command):
        calls.append(command)
        await asyncio.sleep(0.24)
        operation = "version" if command[-1] == "--version" else "status"
        payload = (
            {"sdk_version": "1.0.0"}
            if operation == "version"
            else {"status": "available"}
        )
        return (
            json.dumps(
                {
                    "operation": operation,
                    "profile": "rapp-work-sdk/1",
                    "protocol": "rapp-work/1",
                    "refusal": None,
                    "result": payload,
                    "schema": "rapp-work-result/1",
                    "status": "ok",
                }
            ).encode(),
            0,
        )

    monkeypatch.setattr(runner, "_run_process", delayed_process)
    with pytest.raises(ToolExecutionError, match="deadline"):
        await runner.execute(
            "status",
            {},
            principal="42",
        )

    assert len(calls) == 2


async def test_missing_configured_executable_fails_clearly(settings):
    runner = RappWorkService(
        settings,
        command=str(settings.root / "missing-rapp-work"),
        roots=(settings.root,),
    )

    with pytest.raises(ToolExecutionError, match="executable was not found"):
        await runner.execute("status", {}, principal="42")


def test_absolute_virtualenv_launcher_symlink_is_preserved(settings):
    launcher = settings.root / "venv-python"
    launcher.symlink_to(sys.executable)
    capture = settings.root / "requests.jsonl"
    runner = RappWorkService(
        settings,
        command=shlex.join([str(launcher), str(FAKE_SDK), "normal", str(capture)]),
        roots=(settings.root,),
    )

    assert runner._resolve_command()[0] == str(launcher)


async def test_missing_default_sdk_module_fails_without_fallback(settings, monkeypatch):
    monkeypatch.setattr(
        "rapp_brainstem_gateway.rapp_work.importlib.util.find_spec",
        lambda _: None,
    )
    runner = RappWorkService(settings, roots=(settings.root,))

    with pytest.raises(ToolExecutionError, match="no bundled RAPP/1 fallback"):
        await runner.execute("status", {}, principal="42")


async def test_incompatible_sdk_version_is_refused(settings):
    capture = settings.root / "requests.jsonl"

    with pytest.raises(ToolExecutionError, match="requires SDK 1.0.0"):
        await service(settings, capture, "wrong-version").execute(
            "status",
            {},
            principal="42",
        )

    assert captured(capture) == []
