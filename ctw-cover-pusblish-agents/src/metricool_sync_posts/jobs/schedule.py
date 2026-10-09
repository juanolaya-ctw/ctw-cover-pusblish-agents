"""Job 1: Aprobado - Edición Final → Metricool → Notion Programado."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.attach import resolve_cover_url_for_metricool
from metricool_sync_posts.cover.bridge import prepare_cover_for_publish_task
from metricool_sync_posts.jobs.common import caption_for_row, publication_dt
from metricool_sync_posts.jobs.content_types import build_schedule_body
from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file
from metricool_sync_posts.media.resolve import prepare_media_urls_for_metricool
from metricool_sync_posts.metricool.channels import normalize_channel
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import calendar_week_bounds, now_in, publication_sort_key

logger = logging.getLogger(__name__)


def _publication_on_date(row, tz_name: str, target: date) -> bool:
    pub = publication_dt(row, tz_name)
    if pub is None:
        return False
    return pub.astimezone(ZoneInfo(tz_name)).date() == target


def _norm_channel(name: str | None) -> str:
    """Normalize Canal labels for exclude matching (case/space insensitive)."""
    if not name:
        return ""
    # Collapse unicode spaces / NBSP and trim
    collapsed = re.sub(r"[\s\u00a0]+", " ", name).strip()
    return collapsed.casefold()


def _channel_is_excluded(channel: str | None, exclude: frozenset[str]) -> bool:
    if not exclude:
        return False
    norms = {_norm_channel(x) for x in exclude if _norm_channel(x)}
    return _norm_channel(channel) in norms


def run_schedule(
    *,
    settings: Settings,
    dry_run: bool | None = None,
    only_publication_date: date | None = None,
    exclude_channels: frozenset[str] | None = frozenset(),
) -> dict[str, int]:
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
    fetch_limit = (
        100 if only_publication_date is not None else settings.schedule_max_per_run
    )
    rows = notion.fetch_approved_current_week(week_start, week_end, limit=fetch_limit)
    if only_publication_date is not None:
        tz = settings.timezone
        rows = [r for r in rows if _publication_on_date(r, tz, only_publication_date)]
    exclude = exclude_channels or frozenset()
    if exclude:
        before = len(rows)
        kept = []
        dropped: list[str] = []
        for r in rows:
            if _channel_is_excluded(r.channel, exclude):
                dropped.append(f"{(r.channel or '').strip() or '?'}:{r.page_id[:8]}")
                continue
            kept.append(r)
        rows = kept
        logger.info(
            "Excluded channels filter: %s — dropped %s/%s before media (%s); remaining %s",
            sorted(exclude),
            len(dropped),
            before,
            dropped,
            len(rows),
        )
    rows.sort(
        key=lambda r: (
            publication_sort_key(r.publication, week_start),
            r.page_id,
        )
    )
    stats["queried"] = len(rows)
    logger.info(
        "Found %s approved rows for week %s — %s (channels: %s)",
        len(rows),
        week_start.date(),
        week_end.date(),
        [(r.channel, r.page_id[:8]) for r in rows],
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
            now_local = now_in(settings.timezone)
            if pub < now_local:
                shifted = now_local + timedelta(minutes=5)
                logger.warning(
                    "Publication %s is in the past; scheduling at %s for %s",
                    pub,
                    shifted,
                    row.page_id,
                )
                pub = shifted
            caption = caption_for_row(notion, row)
            if not caption:
                stats["skipped"] += 1
                logger.warning("Skip %s: empty caption", row.page_id)
                continue
            network = normalize_channel(row.channel, title=row.title) or "instagram"
            cover_enabled = (
                network == "instagram"
                and bool((settings.ctw_cover_agent_path or "").strip())
            )
            cover_result = None
            if cover_enabled:
                cover_result = prepare_cover_for_publish_task(settings, row)

            cover_bytes = None
            if cover_result:
                raw = cover_result.get("cover_bytes")
                if isinstance(raw, (bytes, bytearray)):
                    cover_bytes = bytes(raw)

            if (
                cover_enabled
                and settings.require_cover_for_schedule
                and not cover_bytes
            ):
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

            file_ref = classify_final_file(row.final_file_url)
            youtube_existing = file_ref.kind == FinalFileKind.YOUTUBE
            media_urls: list[str] = []
            if row.final_file_url:
                media_urls = prepare_media_urls_for_metricool(
                    settings=settings,
                    metricool=metricool,
                    raw_archivo_final=row.final_file_url,
                    content_type=row.content_type,
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
                media_urls=media_urls,
                cover_url=cover_url,
                youtube_existing_video=youtube_existing,
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
