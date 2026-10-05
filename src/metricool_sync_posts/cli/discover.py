"""List Notion databases to find NOTION_DATABASE_ID."""

from __future__ import annotations

import argparse
import json

from notion_client import Client

from metricool_sync_posts.config import load_settings
from metricool_sync_posts.logging_setup import setup_logging
from metricool_sync_posts.notion.discover_util import search_data_sources, title_from_object


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover Notion data source IDs (use as NOTION_DATABASE_ID)"
    )
    parser.add_argument("--query", default="", help="Search title (Notion API) + local filter")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    client = Client(auth=settings.notion_token)
    q = args.query.lower()
    for ds in search_data_sources(client, query=args.query):
        title = title_from_object(ds)
        if q and q not in title.lower():
            continue
        print(
            json.dumps(
                {"id": ds.get("id"), "title": title, "object": ds.get("object")},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
