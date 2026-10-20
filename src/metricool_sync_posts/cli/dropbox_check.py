"""Check Dropbox sharing.read and list one shared folder. Prints no secrets."""

from __future__ import annotations

import argparse
import sys

from metricool_sync_posts.config import load_settings
from metricool_sync_posts.media.dropbox_host import check_shared_folder, scrub_secrets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the Dropbox refresh token includes sharing.read and can list a shared folder. "
            "Does not print the token, app secret, or rlkey."
        )
    )
    parser.add_argument(
        "shared_link",
        help="Dropbox folder link (/scl/fo/ or /sh/)",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    result = check_shared_folder(settings, args.shared_link)
    print(scrub_secrets(result.message))
    if result.ok:
        for name in result.names:
            print(f"- {name}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
