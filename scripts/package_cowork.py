from __future__ import annotations

import argparse
import binascii
import json
import shutil
import struct
import tempfile
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "cowork" / "appPackage"


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


def validate_manifest(manifest: dict) -> None:
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


def package(mcp_url: str, oauth_reference_id: str, output: Path) -> Path:
    if not mcp_url.startswith("https://") or not mcp_url.endswith("/mcp"):
        raise ValueError("MCP URL must be an HTTPS URL ending in /mcp")
    if not oauth_reference_id.strip():
        raise ValueError("OAuth reference ID is required")

    with tempfile.TemporaryDirectory() as temp_dir:
        staging = Path(temp_dir)
        shutil.copytree(SOURCE / "skills", staging / "skills")
        shutil.copytree(SOURCE / "tools", staging / "tools")
        template = (SOURCE / "manifest.template.json").read_text(encoding="utf-8")
        manifest = json.loads(
            template.replace("__MCP_SERVER_URL__", mcp_url).replace(
                "__OAUTH_REFERENCE_ID__", oauth_reference_id
            )
        )
        validate_manifest(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        write_icon(staging / "color.png", 192, outline=False)
        write_icon(staging / "outline.png", 32, outline=True)

        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
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
    args = parser.parse_args()
    output = package(args.mcp_url, args.oauth_reference_id, args.output)
    print(output)


if __name__ == "__main__":
    main()
