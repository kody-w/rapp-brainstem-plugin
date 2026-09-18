from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def canonical(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def request_from_arguments(arguments: list[str]) -> dict[str, Any]:
    operation = arguments.pop(0)
    request: dict[str, Any] = {"operation": operation}
    names = {
        "--kind": "kind",
        "--max-entries": "max_entries",
        "--mode": "mode",
        "--owner-label": "owner_label",
        "--plan-sha256": "plan_sha256",
        "--root": "root",
        "--slug": "slug",
        "--source": "source",
        "--target": "target",
        "--world-id": "world_id",
    }
    while arguments:
        flag = arguments.pop(0)
        if flag == "--apply":
            request["apply"] = True
            continue
        value = arguments.pop(0)
        if flag == "--plan":
            request["plan"] = json.loads(
                Path(value).read_text(encoding="utf-8"),
                object_pairs_hook=reject_duplicates,
            )
        elif flag == "--root" and operation == "discover":
            request.setdefault("roots", []).append(value)
        else:
            key = names[flag]
            request[key] = int(value) if flag == "--max-entries" else value
    if operation in {"scaffold", "update", "migrate"}:
        request.setdefault("apply", False)
    return request


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    capture = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    if sys.argv[3:] == ["--version"]:
        version = "0.0.0" if mode == "wrong-version" else "1.0.0"
        print(
            canonical(
                {
                    "operation": "version",
                    "profile": "rapp-work-sdk/1",
                    "protocol": "rapp-work/1",
                    "refusal": None,
                    "result": {"sdk_version": version},
                    "schema": "rapp-work-result/1",
                    "status": "ok",
                }
            )
        )
        return
    request = request_from_arguments(sys.argv[3:])
    if capture is not None:
        with capture.open("a", encoding="utf-8") as stream:
            stream.write(canonical(request) + "\n")

    if mode == "nonzero":
        print("refused", file=sys.stderr)
        raise SystemExit(7)
    if mode == "duplicate":
        sys.stdout.write('{"status":"first","status":"second"}')
        return
    if mode == "invalid":
        sys.stdout.write("not-json")
        return
    if mode == "refused-zero":
        print(
            canonical(
                {
                    "operation": request["operation"],
                    "profile": "rapp-work-sdk/1",
                    "protocol": "rapp-work/1",
                    "refusal": {"code": "REFUSE_TEST", "message": "no"},
                    "result": None,
                    "schema": "rapp-work-result/1",
                    "status": "refused",
                }
            )
        )
        return
    if mode == "oversize":
        sys.stdout.write('{"value":"' + ("x" * 8192) + '"}')
        return
    if mode == "timeout":
        time.sleep(2)
    if request["operation"] in {"scaffold", "update", "migrate"}:
        path = {
            "unsafe-git": ".git/config",
            "unsafe-credentials": "config/credentials.json",
            "unsafe-soul": "soul.md",
            "unsafe-brainstem": "src/brainstem.py",
            "unsafe-env": ".env",
        }.get(mode, ".rapp-work/sdk.json")
        plan = {
            "actions": [{"operation": "create", "path": path}],
            "operation": request["operation"],
            "schema": "fake-rapp-work-plan/1",
        }
        digest = hashlib.sha256(canonical(plan).encode("utf-8")).hexdigest()
        if request.get("apply"):
            if request.get("plan") != plan or request.get("plan_sha256") != digest:
                print("apply did not carry the reviewed plan", file=sys.stderr)
                raise SystemExit(8)
            payload = {
                "credential_environment": credential_environment(),
                "request": request,
                "status": "applied",
            }
        else:
            payload = {
                "credential_environment": credential_environment(),
                "plan": plan,
                "plan_sha256": digest,
                "request": request,
                "status": "planned",
            }
    else:
        payload = {
            "credential_environment": credential_environment(),
            "request": request,
            "status": "ok",
        }
    print(
        canonical(
            {
                "operation": request["operation"],
                "profile": "rapp-work-sdk/1",
                "protocol": "rapp-work/1",
                "refusal": None,
                "result": payload,
                "schema": "rapp-work-result/1",
                "status": "applied" if request.get("apply") else (
                    "planned"
                    if request["operation"] in {"scaffold", "update", "migrate"}
                    else "ok"
                ),
            },
        )
    )


def credential_environment() -> list[str]:
    markers = ("AUTH", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
    return sorted(key for key in os.environ if any(marker in key.upper() for marker in markers))


if __name__ == "__main__":
    main()
