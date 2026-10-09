"""Job 2: Notion Programado → infer published in Metricool → Notion Publicado."""

from __future__ import annotations

import logging
from datetime import timedelta

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.common import (
    caption_for_row,
    metricool_fetch_window_for_confirm,
    publication_dt,
)
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.matching import find_best_match, post_state
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import now_in, publication_window

logger = logging.getLogger(__name__)


def run_confirm_published(*, settings: Settings, dry_run: bool | None = None) -> dict[str, int]:
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {"queried": 0, "published": 0, "pending": 0, "skipped": 0, "errors": 0}

    notion = NotionRepository(settings)
    metricool = MetricoolClient(settings)
    dedupe = DedupeStore(
        path=settings.slack_dedupe_file,
        ttl_seconds=settings.slack_dedupe_hours * 3600,
    )

    now = now_in(settings.timezone)
    center = now.date()
    win_start, win_end = publication_window(
        center, settings.timezone, settings.publication_window_days
    )
    rows = notion.fetch_scheduled_in_window(
        win_start, win_end, limit=settings.confirm_max_per_run
    )
    stats["queried"] = len(rows)

    mc_start, mc_end = metricool_fetch_window_for_confirm(settings)
    mc_posts = metricool.get_scheduled_posts(mc_start, mc_end, extended_range=True)
    logger.info("Loaded %s Metricool posts for matching", len(mc_posts))

    for row in rows:
        try:
            pub = publication_dt(row, settings.timezone)
            if pub is None:
                stats["skipped"] += 1
                continue
            caption = caption_for_row(notion, row)
            match = find_best_match(
                notion_caption=caption,
                notion_channel=row.channel,
                notion_publication=pub,
                candidates=mc_posts,
                tz_name=settings.timezone,
            )
            if not match:
                stats["skipped"] += 1
                logger.info("No Metricool match for Notion page %s", row.page_id)
                continue
            state = post_state(match)
            if state == "ERROR":
                stats["skipped"] += 1
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="metricool_error",
                    message=f"Metricool ERROR for {row.url}; left as Programado",
                    dry_run=dry,
                )
                continue
            if state == "PENDING":
                stats["pending"] += 1
                continue
            if state in ("PUBLISHED", "UNKNOWN"):
                # UNKNOWN: if publication date is in the past, treat as published
                if state == "UNKNOWN" and pub > now - timedelta(minutes=30):
                    stats["pending"] += 1
                    continue
                if dry:
                    logger.info("[dry-run] Would mark Publicado %s", row.page_id)
                else:
                    notion.set_status(row.page_id, settings.notion_status_published, dry_run=False)
                stats["published"] += 1
            else:
                stats["pending"] += 1
        except Exception:
            stats["errors"] += 1
            logger.exception("confirm_published failed for %s", row.page_id)

    metricool.close()
    return stats
