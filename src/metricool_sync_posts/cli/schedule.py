from __future__ import annotations

import argparse

from metricool_sync_posts.cli._args import add_dry_run, add_enable_schedule
from metricool_sync_posts.config import load_settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule approved Notion posts in Metricool")
    add_dry_run(parser)
    add_enable_schedule(parser)
    args = parser.parse_args()
    settings = load_settings()
    if args.enable:
        settings.enable_schedule = True
        # Same default as env: require cover when schedule is enabled
        if settings.require_cover_for_schedule_override is None:
            settings.require_cover_for_schedule_override = True
    setup_logging(settings.log_level)
    stats = run_schedule(settings=settings, dry_run=args.dry_run or settings.dry_run)
    print(stats)


if __name__ == "__main__":
    main()
