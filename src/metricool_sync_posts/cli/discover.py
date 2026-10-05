"""List Notion databases to find NOTION_DATABASE_ID."""

from __future__ import annotations

import argparse
import json

from notion_client import Client

from metricool_sync_posts.config import load_settings
from metricool_sync_posts.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Notion database IDs")
    parser.add_argument("--query", default="", help="Filter database titles (case-insensitive)")
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)
    client = Client(auth=settings.notion_token)
    resp = client.search(filter={"property": "object", "value": "database"}, page_size=100)
    q = args.query.lower()
    for db in resp.get("results") or []:
        title_parts = (db.get("title") or []) if isinstance(db.get("title"), list) else []
        title = "".join(p.get("plain_text", "") for p in title_parts)
        if q and q not in title.lower():
            continue
        print(json.dumps({"id": db.get("id"), "title": title}, ensure_ascii=False))


if __name__ == "__main__":
    main()
