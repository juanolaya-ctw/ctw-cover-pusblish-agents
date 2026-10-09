from __future__ import annotations

import argparse
from datetime import date

from metricool_sync_posts.cli._args import add_dry_run, add_enable_schedule
from metricool_sync_posts.config import load_settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule approved Notion posts in Metricool")
    add_dry_run(parser)
    add_enable_schedule(parser)
    parser.add_argument(
        "--only-date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Only rows whose Publicación falls on this date (America/Bogota)",
    )
    parser.add_argument(
        "--exclude-channel",
        action="append",
        default=[],
        metavar="NAME",
        help="Skip rows whose Canal matches exactly (repeatable)",
    )
    args = parser.parse_args()
    settings = load_settings()
    if args.enable:
        settings.enable_schedule = True
        # Covers stay optional unless REQUIRE_COVER_FOR_SCHEDULE=true (Phase 2)
    setup_logging(settings.log_level)
    exclude = settings.schedule_exclude_channels_set() | frozenset(args.exclude_channel)
    stats = run_schedule(
        settings=settings,
        dry_run=args.dry_run or settings.dry_run,
        only_publication_date=args.only_date,
        exclude_channels=exclude,
    )
    print(stats)


if __name__ == "__main__":
    main()
