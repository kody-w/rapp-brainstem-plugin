from __future__ import annotations

import argparse

from package_cowork import validate_release_manifest, write_release_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or verify the deterministic RAPP Work plugin release manifest"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument(
        "--allow-unreleased-sdk-pin-for-development",
        action="store_true",
        help=(
            "Permit an explicit unreleased SDK pin only for non-release "
            "development validation"
        ),
    )
    args = parser.parse_args()
    options = {
        "allow_unreleased_sdk_pin": args.allow_unreleased_sdk_pin_for_development,
    }
    if args.write:
        print(write_release_manifest(**options))
    else:
        validate_release_manifest(**options)
        print("RAPP_WORK_PLUGIN_RELEASE.json verified")


if __name__ == "__main__":
    main()
