"""List Notion databases to find NOTION_DATABASE_ID."""

from __future__ import annotations

import argparse
import json
import sys

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
    sources = search_data_sources(client, query=args.query)
    shown = 0
    for ds in sources:
        title = title_from_object(ds)
        if q and title and q not in title.lower():
            continue
        print(
            json.dumps(
                {"id": ds.get("id"), "title": title or "(sin título)", "object": ds.get("object")},
                ensure_ascii=False,
            )
        )
        shown += 1
    if shown == 0:
        print(
            "No hay data sources visibles para esta integración.\n"
            "Checklist:\n"
            "  1. NOTION_TOKEN = secret de la integración correcta (workspace CTW).\n"
            "  2. En Notion: Parrilla CTW → ⋯ → Connections → añadir la integración.\n"
            "  3. Repetir: python3 -m metricool_sync_posts.cli.discover --query Parrilla\n"
            "  4. Si sigue vacío, probar sin filtro: python3 -m metricool_sync_posts.cli.discover",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
