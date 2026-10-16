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
from metricool_sync_posts.media.expiry import due_within_24h, media_url_live, post_media_urls
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.matching import (
    combined_post_state,
    find_best_match,
    find_sync_match,
    post_publication_datetime,
    post_state,
    tiktok_manual_publish,
    youtube_split_companions,
)
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import now_in, publication_window

logger = logging.getLogger(__name__)

# A post that left the scheduler counts as published only after this grace.
_VANISHED_GRACE = timedelta(minutes=30)


def _warn_if_media_expired(
    *,
    settings: Settings,
    dedupe: DedupeStore,
    row,
    post: dict,
    publication,
    now,
    dry: bool,
    stats: dict[str, int],
) -> None:
    """HEAD/GET media on pipeline posts that publish within 24 hours."""
    if not due_within_24h(publication, now):
        return
    urls = post_media_urls(post)
    if not urls:
        return
    dead: list[str] = []
    for url in urls:
        live, status = media_url_live(url)
        if not live:
            dead.append(f"{url} (HTTP {status})")
    if not dead:
        return
    stats["media_expired"] = stats.get("media_expired", 0) + 1
    detail = "; ".join(dead)
    logger.error(
        "media_expired page=%s notion=%s urls=%s",
        row.page_id,
        row.url,
        detail,
    )
    notify_slack(
        webhook_url=settings.slack_webhook_url,
        channel=settings.slack_channel,
        dedupe=dedupe,
        notion_page_id=row.page_id,
        reason="media_expired",
        message=(
            f"{row.url}: el archivo del post ya no está disponible y se publica en menos "
            f"de 24 h. Vuelve a subir el archivo en Metricool. Detalle: {detail}"
        ),
        dry_run=dry,
    )


def _mark_published(notion: NotionRepository, settings: Settings, page_id: str, dry: bool) -> None:
    if dry:
        logger.info("[dry-run] Would mark Publicado %s", page_id)
        return
    notion.set_status(page_id, settings.notion_status_published, dry_run=False)


def run_confirm_published(*, settings: Settings, dry_run: bool | None = None) -> dict[str, int]:
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {
        "queried": 0,
        "published": 0,
        "pending": 0,
        "skipped": 0,
        "errors": 0,
        "blockers": 0,
        "media_expired": 0,
    }

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
            if match is None:
                # Date drift (Notion 12:00 vs Metricool 15:00) must not look like
                # a vanished post. Match on copy inside the fetched window.
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
                if pub <= now - _VANISHED_GRACE:
                    # No confident match: never mark Publicado blindly. The piece
                    # may never have been published (Oct 2026 audit: SpaceX).
                    logger.warning(
                        "No confident Metricool match for %s and publication %s has "
                        "passed; left as Programado and alerted",
                        row.page_id,
                        pub,
                    )
                    stats["missing"] = stats.get("missing", 0) + 1
                    title = (getattr(row, "title", None) or "").strip() or "Pieza sin nombre"
                    notify_slack(
                        webhook_url=settings.slack_webhook_url,
                        channel=settings.slack_channel,
                        dedupe=dedupe,
                        notion_page_id=row.page_id,
                        reason="missing_in_metricool",
                        message=(
                            f"<{row.url}|{title}>: está en Programado en Notion, pero no "
                            "encontré ningún post en Metricool con ese texto. Revisa si "
                            "salió: si salió, pásala a Publicado; si no, ponle una hora "
                            "futura y pásala a Aprobado - Edición Final."
                        ),
                        dry_run=dry,
                    )
                else:
                    stats["skipped"] += 1
                    logger.info("No Metricool match for Notion page %s", row.page_id)
                continue
            # A reel cover and YouTube are two posts. Both have to be published.
            group = youtube_split_companions(
                match,
                mc_posts,
                notion_channel=row.channel,
                tz_name=settings.timezone,
            )
            state = combined_post_state(group)
            if state == "ERROR":
                stats["blockers"] += 1
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="metricool_error",
                    message=(
                        f"{row.url}: el post salió con ERROR en Metricool; Notion sigue "
                        "en Programado. Revisa el post en Metricool y vuelve a publicarlo."
                    ),
                    dry_run=dry,
                )
                continue
            if state == "PUBLISHED":
                # Manual TikTok: status Published, but no TikTok post id or link.
                if any(tiktok_manual_publish(item) for item in group):
                    stats["tiktok_manual"] = stats.get("tiktok_manual", 0) + 1
                    stats["pending"] += 1
                    logger.warning(
                        "TikTok manual publish for %s; left as Programado",
                        row.page_id,
                    )
                    title = (getattr(row, "title", None) or "").strip() or "Pieza sin nombre"
                    page = getattr(row, "url", None) or row.page_id
                    label = f"<{page}|{title}>" if getattr(row, "url", None) else title
                    notify_slack(
                        webhook_url=settings.slack_webhook_url,
                        channel=settings.slack_channel,
                        dedupe=dedupe,
                        notion_page_id=row.page_id,
                        reason="tiktok_manual_publish",
                        message=(
                            f"{label}: TikTok quedó en publicación manual / no salió. "
                            "Metricool lo marca como Published, pero no hay id ni enlace "
                            "de TikTok. Notion sigue en Programado."
                        ),
                        dry_run=dry,
                    )
                    continue
                _mark_published(notion, settings, row.page_id, dry)
                stats["published"] += 1
                continue
            # PENDING, SCHEDULED, UNKNOWN: a found post is not confirmation.
            # UNKNOWN used to be treated as published once the date was past.
            # A YouTube thumbnail warning is PUBLISHED, so it does not alert.
            if state == "PENDING":
                for item in group:
                    if post_state(item) != "PENDING":
                        continue
                    mc_pub = post_publication_datetime(item, settings.timezone)
                    _warn_if_media_expired(
                        settings=settings,
                        dedupe=dedupe,
                        row=row,
                        post=item,
                        publication=mc_pub or pub,
                        now=now,
                        dry=dry,
                        stats=stats,
                    )
            stats["pending"] += 1
        except Exception:
            stats["errors"] += 1
            logger.exception("confirm_published failed for %s", row.page_id)

    metricool.close()
    return stats
