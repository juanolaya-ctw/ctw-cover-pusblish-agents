"""Job 1: Aprobado - Edición Final → Metricool → Notion Programado."""

from __future__ import annotations

import logging

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.attach import resolve_cover_url_for_metricool
from metricool_sync_posts.cover.bridge import prepare_cover_for_publish_task
from metricool_sync_posts.jobs.common import caption_for_row, publication_dt
from metricool_sync_posts.jobs.content_types import build_schedule_body
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import calendar_week_bounds, now_in

logger = logging.getLogger(__name__)


def run_schedule(*, settings: Settings, dry_run: bool | None = None) -> dict[str, int]:
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {"queried": 0, "scheduled": 0, "skipped": 0, "errors": 0}

    if not settings.enable_schedule:
        logger.warning("Schedule job disabled (ENABLE_SCHEDULE=false). Exiting.")
        return stats

    notion = NotionRepository(settings)
    metricool = MetricoolClient(settings)
    dedupe = DedupeStore(
        path=settings.slack_dedupe_file,
        ttl_seconds=settings.slack_dedupe_hours * 3600,
    )
    settings.ensure_data_dirs()

    now = now_in(settings.timezone)
    week_start, week_end = calendar_week_bounds(now, settings.timezone)
    rows = notion.fetch_approved_current_week(
        week_start, week_end, limit=settings.schedule_max_per_run
    )
    stats["queried"] = len(rows)
    logger.info(
        "Found %s approved rows for week %s — %s",
        len(rows),
        week_start.date(),
        week_end.date(),
    )

    for row in rows:
        try:
            pub = publication_dt(row, settings.timezone)
            if pub is None:
                stats["skipped"] += 1
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="missing_publication_date",
                    message=f"Schedule skip: missing Publicación for {row.url}",
                    dry_run=dry,
                )
                continue
            caption = caption_for_row(notion, row)
            if not caption:
                stats["skipped"] += 1
                logger.warning("Skip %s: empty caption", row.page_id)
                continue
            cover_enabled = bool((settings.ctw_cover_agent_path or "").strip())
            cover_result = None
            if cover_enabled:
                cover_result = prepare_cover_for_publish_task(settings, row)

            cover_bytes = None
            if cover_result:
                raw = cover_result.get("cover_bytes")
                if isinstance(raw, (bytes, bytearray)):
                    cover_bytes = bytes(raw)

            if cover_enabled and settings.require_cover_for_schedule and not cover_bytes:
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: REQUIRE_COVER_FOR_SCHEDULE and no cover_bytes from agent",
                    row.page_id,
                )
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="missing_cover",
                    message=f"Schedule skip: missing cover for {row.url}",
                    dry_run=dry,
                )
                continue

            media_url = None
            if row.final_file_url:
                media_url = prepare_media_for_metricool(
                    settings=settings,
                    metricool=metricool,
                    source_url=row.final_file_url,
                    dry_run=dry,
                )

            cover_url = None
            if cover_bytes:
                cover_url = resolve_cover_url_for_metricool(
                    settings=settings,
                    metricool=metricool,
                    cover_bytes=cover_bytes,
                    page_id=row.page_id,
                    dry_run=dry,
                )

            body = build_schedule_body(
                caption=caption,
                publication=pub,
                tz_name=settings.timezone,
                channel=row.channel,
                title=row.title,
                content_type=row.content_type,
                media_url=media_url,
                cover_url=cover_url,
            )
            if dry:
                logger.info("[dry-run] Would schedule Metricool post for %s", row.page_id)
            else:
                metricool.create_scheduled_post(body)
                notion.set_status(row.page_id, settings.notion_status_scheduled, dry_run=False)
            stats["scheduled"] += 1
            notify_slack(
                webhook_url=settings.slack_webhook_url,
                channel=settings.slack_channel,
                dedupe=dedupe,
                notion_page_id=row.page_id,
                reason="scheduled",
                message=f"Programado en Metricool: {row.title or row.page_id}",
                dry_run=dry,
            )
        except Exception as exc:
            stats["errors"] += 1
            logger.exception("Schedule failed for %s: %s", row.page_id, exc)
            notify_slack(
                webhook_url=settings.slack_webhook_url,
                channel=settings.slack_channel,
                dedupe=dedupe,
                notion_page_id=row.page_id,
                reason="schedule_error",
                message=f"Error programando {row.url}: {exc}",
                dry_run=dry,
            )

    metricool.close()
    return stats
