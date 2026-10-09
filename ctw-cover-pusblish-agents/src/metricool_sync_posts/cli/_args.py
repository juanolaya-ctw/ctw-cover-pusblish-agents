import argparse


def add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without writing to Notion/Metricool/Slack",
    )


def add_enable_schedule(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--enable",
        action="store_true",
        help="Override ENABLE_SCHEDULE=false for this run only",
    )
