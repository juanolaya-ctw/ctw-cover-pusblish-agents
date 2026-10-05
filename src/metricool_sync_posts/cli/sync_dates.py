from __future__ import annotations

import argparse

from metricool_sync_posts.cli._args import add_dry_run
from metricool_sync_posts.config import load_settings
from metricool_sync_posts.jobs.sync_dates import run_sync_dates
from metricool_sync_posts.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Notion publication dates to Metricool")
    add_dry_run(parser)
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    stats = run_sync_dates(settings=settings, dry_run=args.dry_run or settings.dry_run)
    print(stats)


if __name__ == "__main__":
    main()
