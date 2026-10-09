"""Job 3: sync Notion Publicación date to Metricool when still schedulable."""

from __future__ import annotations

import logging

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.common import (
    caption_for_row,
    metricool_fetch_window_for_sync,
    publication_dt,
)
from metricool_sync_posts.jobs.content_types import merge_publication_date
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.matching import (
    find_sync_match,
    post_publication_datetime,
    post_state,
)
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.timeutil import dates_equal_within_minutes, now_in, publication_window

logger = logging.getLogger(__name__)


def run_sync_dates(*, settings: Settings, dry_run: bool | None = None) -> dict[str, int]:
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {"queried": 0, "updated": 0, "skipped": 0, "errors": 0}

    notion = NotionRepository(settings)
    metricool = MetricoolClient(settings)

    now = now_in(settings.timezone)
    win_start, win_end = publication_window(
        now.date(), settings.timezone, settings.publication_window_days
    )
    rows = notion.fetch_scheduled_in_window(
        win_start, win_end, limit=settings.sync_dates_max_per_run
    )
    stats["queried"] = len(rows)

    mc_start, mc_end = metricool_fetch_window_for_sync(settings)
    mc_posts = metricool.get_scheduled_posts(mc_start, mc_end, extended_range=True)

    for row in rows:
        try:
            pub = publication_dt(row, settings.timezone)
            if pub is None:
                stats["skipped"] += 1
                continue
            caption = caption_for_row(notion, row)
            match = find_sync_match(
                notion_caption=caption,
                notion_title=row.title,
                notion_channel=row.channel,
                notion_publication=pub,
                candidates=mc_posts,
                tz_name=settings.timezone,
                metricool_id=getattr(row, "metricool_id", None),
                metricool_uuid=getattr(row, "metricool_uuid", None),
                window_days=settings.publication_window_days,
            )
            if not match:
                stats["skipped"] += 1
                continue
            if post_state(match) == "PUBLISHED":
                stats["skipped"] += 1
                logger.info("Skip date sync: already published %s", row.page_id)
                continue
            if post_state(match) == "ERROR":
                stats["skipped"] += 1
                logger.error(
                    "BLOCKER: skip date sync for %s; Metricool provider ERROR",
                    row.page_id,
                )
                continue
            mc_pub = post_publication_datetime(match, settings.timezone)
            if mc_pub and dates_equal_within_minutes(pub, mc_pub, minutes=1):
                stats["skipped"] += 1
                continue
            post_id = str(match.get("id") or match.get("postId") or "")
            uuid = match.get("uuid")
            if not post_id:
                stats["skipped"] += 1
                continue
            full_body = merge_publication_date(match, pub, settings.timezone)
            if dry:
                logger.info(
                    "[dry-run] Would update Metricool post %s publication to %s (Notion source)",
                    post_id,
                    pub,
                )
            else:
                metricool.update_scheduled_post(
                    post_id,
                    full_body,
                    uuid=str(uuid) if uuid else None,
                )
            stats["updated"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.exception("sync_dates failed for %s: %s", row.page_id, exc)

    metricool.close()
    return stats
