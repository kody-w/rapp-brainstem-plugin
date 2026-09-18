from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).resolve().parent


def validate_release_provenance() -> None:
    inserted = str(ROOT) not in sys.path
    if inserted:
        sys.path.insert(0, str(ROOT))
    try:
        from scripts.package_cowork import validate_release_manifest

        validate_release_manifest(
            allow_unreleased_sdk_pin=(
                os.getenv("RAPP_WORK_ALLOW_UNRELEASED_SDK_PIN_FOR_DEVELOPMENT") == "1"
            )
        )
    finally:
        if inserted:
            sys.path.remove(str(ROOT))


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        validate_release_provenance()
