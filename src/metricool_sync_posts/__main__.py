"""python -m metricool_sync_posts dropbox-check <shared link>"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m metricool_sync_posts dropbox-check <shared-link>",
            file=sys.stdout if args else sys.stderr,
        )
        return 0 if args else 2
    if args[0] != "dropbox-check":
        print(f"unknown command: {args[0]}", file=sys.stderr)
        print(
            "usage: python -m metricool_sync_posts dropbox-check <shared-link>",
            file=sys.stderr,
        )
        return 2
    from metricool_sync_posts.cli.dropbox_check import main as check_main

    return check_main(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
