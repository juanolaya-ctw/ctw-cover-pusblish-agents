from __future__ import annotations

import argparse
import logging
from datetime import date
from uuid import UUID

from metricool_sync_posts.build_info import build_label
from metricool_sync_posts.cli._args import add_dry_run, add_enable_schedule
from metricool_sync_posts.config import load_settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.logging_setup import setup_logging

logger = logging.getLogger(__name__)


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
    parser.add_argument(
        "--only-page-id",
        type=lambda value: str(UUID(value)),
        metavar="UUID",
        help="Only one approved row this week; fails closed if missing or excluded",
    )
    args = parser.parse_args()
    settings = load_settings()
    if args.enable:
        settings.enable_schedule = True
    setup_logging(settings.log_level)
    logger.info("metricool_sync_posts build: %s", build_label())
    exclude = settings.schedule_exclude_channels_set() | frozenset(args.exclude_channel)
    stats = run_schedule(
        settings=settings,
        dry_run=args.dry_run or settings.dry_run,
        only_publication_date=args.only_date,
        only_page_id=args.only_page_id,
        exclude_channels=exclude,
    )
    print(stats)


if __name__ == "__main__":
    main()
