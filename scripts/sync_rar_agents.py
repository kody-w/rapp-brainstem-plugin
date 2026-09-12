"""Fetch the reviewed RAR defaults at their locked revision, or check them offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_source(source: bytes, expected: str, name: str) -> bytes:
    source = source.replace(b"\r\n", b"\n")
    if hashlib.sha256(source).hexdigest() != expected:
        raise ValueError(f"RAR source hash mismatch for {name}; refusing unverified bytes.")
    return source


def sync(*, check: bool = False, root: Path = ROOT) -> None:
    agents_path = root / "agents"
    lock = json.loads((agents_path / "defaults.lock.json").read_text(encoding="utf-8"))
    sources: list[tuple[Path, bytes]] = []
    for entry in lock["agents"]:
        destination = agents_path / entry["file"]
        if destination.parent != agents_path or destination.suffix != ".py":
            raise ValueError("Default agents must be Python files directly inside agents/.")
        if check:
            source = destination.read_bytes()
        else:
            url = (
                f"https://raw.githubusercontent.com/{lock['repository']}/"
                f"{lock['revision']}/{entry['source']}"
            )
            with urllib.request.urlopen(url, timeout=15) as response:
                source = response.read()
        sources.append((destination, verify_source(source, entry["sha256"], entry["name"])))

    if not check:
        for destination, source in sources:
            destination.write_bytes(source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify local hashes without networking."
    )
    args = parser.parse_args()
    sync(check=args.check)
    print(
        "RAR default agent hashes verified." if args.check else "RAR default agents synchronized."
    )


if __name__ == "__main__":
    main()
