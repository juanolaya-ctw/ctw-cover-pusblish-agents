from __future__ import annotations

import logging
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError

from metricool_sync_posts.config import Settings
from metricool_sync_posts.notion.properties import NotionPostRow, row_from_page
from metricool_sync_posts.notion.queries import (
    and_filter,
    status_equals,
    status_equals_select,
    week_publication_filter,
    window_publication_filter,
)
from metricool_sync_posts.timeutil import publication_sort_key

logger = logging.getLogger(__name__)


class NotionRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Client(auth=settings.notion_token)
        self._prop_names = {
            "status": settings.notion_prop_status,
            "publication": settings.notion_prop_publication,
            "channel": settings.notion_prop_channel,
            "caption": settings.notion_prop_caption,
            "final_file": settings.notion_prop_final_file,
            "title": settings.notion_prop_title,
            "content_type": settings.notion_prop_content_type,
            "miniatura": settings.notion_prop_miniatura,
            "protagonista": settings.notion_prop_protagonista,
            "metricool_id": settings.notion_prop_metricool_id,
            "metricool_uuid": settings.notion_prop_metricool_uuid,
        }

    def _query(
        self,
        filter_obj: dict[str, Any],
        *,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            query_kwargs: dict[str, Any] = {
                "filter": filter_obj,
                "page_size": min(page_size, 100),
            }
            if cursor:
                query_kwargs["start_cursor"] = cursor
            resp = self._client.data_sources.query(
                self._settings.notion_database_id,
                **query_kwargs,
            )
            results.extend(resp.get("results") or [])
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    def _status_filter(self, status_value: str) -> dict[str, Any]:
        # Try status property first (Notion native status)
        return status_equals(self._settings.notion_prop_status, status_value)

    def _status_filter_with_fallback(self, status_value: str) -> list[dict[str, Any]]:
        return [
            status_equals(self._settings.notion_prop_status, status_value),
            status_equals_select(self._settings.notion_prop_status, status_value),
        ]

    def fetch_approved_current_week(
        self,
        week_start,
        week_end,
        *,
        limit: int | None,
    ) -> list[NotionPostRow]:
        pub = week_publication_filter(
            self._settings.notion_prop_publication, week_start, week_end
        )
        rows: list[NotionPostRow] = []
        for status_f in self._status_filter_with_fallback(self._settings.notion_status_approved):
            filt = and_filter(status_f, pub)
            try:
                pages = self._query(filt)
            except APIResponseError as exc:
                if exc.code == "validation_error":
                    continue
                raise
            for page in pages:
                rows.append(row_from_page(page, self._prop_names))
            if rows:
                break
        rows.sort(
            key=lambda r: (publication_sort_key(r.publication, week_start), r.page_id)
        )
        return rows if limit is None else rows[:limit]

    def fetch_scheduled_in_window(
        self,
        window_start,
        window_end,
        *,
        limit: int,
    ) -> list[NotionPostRow]:
        pub = window_publication_filter(
            self._settings.notion_prop_publication, window_start, window_end
        )
        rows: list[NotionPostRow] = []
        for status_f in self._status_filter_with_fallback(self._settings.notion_status_scheduled):
            filt = and_filter(status_f, pub)
            try:
                pages = self._query(filt)
            except APIResponseError as exc:
                if exc.code == "validation_error":
                    continue
                raise
            for page in pages:
                rows.append(row_from_page(page, self._prop_names))
            if rows:
                break
        rows.sort(
            key=lambda r: (publication_sort_key(r.publication, window_start), r.page_id)
        )
        return rows[:limit]

    def page_body_plain_text(self, page_id: str) -> str:
        """Fallback caption from page blocks (first paragraphs only, capped)."""
        parts: list[str] = []
        cursor: str | None = None
        while len(parts) < 20:
            kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 50}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self._client.blocks.children.list(**kwargs)
            for block in resp.get("results") or []:
                t = block.get("type")
                rich = block.get(t, {}) if t else {}
                if isinstance(rich, dict) and "rich_text" in rich:
                    text = "".join(p.get("plain_text", "") for p in rich["rich_text"])
                    if text.strip():
                        parts.append(text.strip())
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return "\n\n".join(parts)[:8000]

    def set_status(self, page_id: str, status: str, *, dry_run: bool) -> None:
        prop = self._settings.notion_prop_status
        payload_status = {"status": {"name": status}}
        payload_select = {"select": {"name": status}}
        if dry_run:
            logger.info("[dry-run] Notion set %s -> %s", page_id, status)
            return
        for payload in (payload_status, payload_select):
            try:
                self._client.pages.update(
                    page_id=page_id,
                    properties={prop: payload},
                )
                return
            except APIResponseError as exc:
                if exc.code == "validation_error":
                    continue
                raise
        raise RuntimeError(f"Could not update status property {prop!r} on page {page_id}")

    def list_databases(self) -> list[dict[str, Any]]:
        """Search workspace for data sources (discover helper)."""
        from metricool_sync_posts.notion.discover_util import search_data_sources

        return search_data_sources(self._client)
