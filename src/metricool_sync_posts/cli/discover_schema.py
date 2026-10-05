"""Print Notion data source property names (map to NOTION_PROP_* in .env)."""

from __future__ import annotations

import argparse
import json

from notion_client import Client

from metricool_sync_posts.config import load_settings
from metricool_sync_posts.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="List Notion data source schema properties")
    args = parser.parse_args()
    _ = args
    settings = load_settings()
    setup_logging(settings.log_level)
    client = Client(auth=settings.notion_token)
    ds = client.data_sources.retrieve(settings.notion_database_id)
    props = ds.get("properties") or {}
    rows: list[dict[str, str]] = []
    for name, meta in props.items():
        if isinstance(meta, dict):
            rows.append({"name": name, "type": str(meta.get("type", ""))})
        else:
            rows.append({"name": name, "type": ""})
    rows.sort(key=lambda r: r["name"].lower())
    payload = {"data_source_id": settings.notion_database_id, "properties": rows}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(
        "\nMap in .env, e.g. NOTION_PROP_STATUS=<name of status/select column>",
        flush=True,
    )


if __name__ == "__main__":
    main()
