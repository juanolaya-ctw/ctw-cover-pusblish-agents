"""Notion search helpers (API 2025-09-03: data_source, not database)."""

from __future__ import annotations

from typing import Any

from notion_client import Client


def title_from_object(obj: dict[str, Any]) -> str:
    title = obj.get("title")
    if isinstance(title, list):
        return "".join(p.get("plain_text", "") for p in title if isinstance(p, dict))
    if isinstance(title, str):
        return title
    name = obj.get("name")
    return str(name) if name else ""


def search_data_sources(
    client: Client,
    *,
    query: str = "",
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """List data sources shared with the integration (Parrilla IDs for NOTION_DATABASE_ID)."""
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    q = query.strip()
    while True:
        kwargs: dict[str, Any] = {
            "filter": {"property": "object", "value": "data_source"},
            "page_size": min(page_size, 100),
        }
        if q:
            kwargs["query"] = q
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.search(**kwargs)
        results.extend(resp.get("results") or [])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return results
