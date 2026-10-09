"""Job 1: Aprobado - Edición Final → Metricool → Notion Programado."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from metricool_sync_posts.build_info import build_label
from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.reel import resolve_instagram_reel_cover
from metricool_sync_posts.jobs.common import (
    caption_for_row,
    caption_has_placeholder,
    publication_dt,
)
from metricool_sync_posts.jobs.content_types import build_schedule_body, is_miniatura_type
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard
from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file
from metricool_sync_posts.media.resolve import prepare_media_urls_for_metricool
from metricool_sync_posts.metricool.channels import canal_labels, normalize_channel, plan_canals
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.matching import (
    find_duplicate_candidates,
    find_slot_occupants,
    format_match_evidence,
    post_state,
)
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import calendar_week_bounds, now_in, publication_sort_key

logger = logging.getLogger(__name__)

# Business rules, not just defaults: Newsletter stays manual, IG Nico is another brand.
_ALWAYS_EXCLUDED = frozenset({"Newsletter", "IG Nico"})


def _publication_on_date(row, tz_name: str, target: date) -> bool:
    pub = publication_dt(row, tz_name)
    if pub is None:
        return False
    return pub.astimezone(ZoneInfo(tz_name)).date() == target


def _norm_channel(name: str | None) -> str:
    """Normalize Canal labels for exclude matching (case/space insensitive)."""
    if not name:
        return ""
    collapsed = re.sub(r"[\s\u00a0]+", " ", name).strip()
    return collapsed.casefold()


def _channel_is_excluded(channel: str | None, exclude: frozenset[str]) -> bool:
    if not exclude:
        return False
    norms = {_norm_channel(x) for x in exclude if _norm_channel(x)}
    return _norm_channel(channel) in norms


def _is_linkedin_only_canal(channel: str | None) -> bool:
    """True for Canal values that are LinkedIn and nothing else."""
    if not channel or "linkedin" not in _norm_channel(channel):
        return False
    return normalize_channel(channel) == "linkedin"


def _row_excluded(channel: str | None, exclude: frozenset[str]) -> bool:
    return _channel_is_excluded(channel, exclude) or _is_linkedin_only_canal(channel)


def _row_should_drop(row, exclude: frozenset[str]) -> bool:
    """Drop before media when every Canal value is excluded, or IG Nico is present.

    Unknown labels stay in the loop so the skip is logged. A mix of Newsletter
    or LinkedIn with a real network is kept; those labels are removed later.
    """
    labels = canal_labels(row)
    if not labels:
        return False
    plan = plan_canals(labels, exclude=exclude)
    if plan.skip_nico:
        return True
    return not plan.networks and not plan.unrecognized


def _as_post_list(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _post_key(post: dict) -> str:
    return str(post.get("id") or post.get("postId") or "")


def _linked_rows(notion, window_start, window_end) -> list:
    """Programado/Publicado rows. A missing or unexpected client result means none."""
    fetcher = getattr(notion, "fetch_linked_in_window", None)
    if not callable(fetcher):
        return []
    try:
        rows = fetcher(window_start, window_end)
    except Exception:
        logger.exception("Could not load Programado/Publicado rows for duplicate claims")
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if getattr(row, "page_id", None)]


def _claim_linked_posts(
    *,
    notion,
    linked_rows: list,
    approved_ids: set[str],
    existing_posts: list[dict],
    exclude: frozenset[str],
    tz_name: str,
) -> dict[str, str]:
    """Metricool post id → Notion page that already owns it."""
    claimed: dict[str, str] = {}
    for linked in linked_rows:
        if linked.page_id in approved_ids:
            continue
        try:
            caption = caption_for_row(notion, linked)
        except Exception:
            logger.exception("Caption failed for linked row %s", linked.page_id)
            continue
        if not caption:
            continue
        plan = plan_canals(canal_labels(linked), exclude=exclude)
        if plan.skip_nico or not plan.networks:
            continue
        hits = find_duplicate_candidates(
            existing_posts,
            caption=caption,
            title=getattr(linked, "title", None),
            network=None,
            networks=list(plan.networks),
            tz_name=tz_name,
            notion_publication=publication_dt(linked, tz_name),
            media_urls=[linked.final_file_url] if getattr(linked, "final_file_url", None) else None,
        )
        stored_id = str(getattr(linked, "metricool_id", None) or "").strip()
        stored_uuid = str(getattr(linked, "metricool_uuid", None) or "").strip()
        if stored_id or stored_uuid:
            for post in existing_posts:
                post_uuid = str(post.get("uuid") or "").strip()
                if (stored_id and _post_key(post) == stored_id) or (
                    stored_uuid and post_uuid == stored_uuid
                ):
                    hits.append(post)
        for hit in hits:
            key = _post_key(hit)
            if not key or key in claimed:
                continue
            claimed[key] = linked.page_id
            logger.info(
                "Match Notion %s -> Metricool %s %s",
                linked.page_id,
                key,
                format_match_evidence(
                    hit,
                    caption=caption,
                    title=getattr(linked, "title", None),
                    tz_name=tz_name,
                    notion_publication=publication_dt(linked, tz_name),
                    networks=list(plan.networks),
                    media_urls=(
                        [linked.final_file_url] if getattr(linked, "final_file_url", None) else None
                    ),
                    stored_id=stored_id or None,
                    stored_uuid=stored_uuid or None,
                ),
            )
    return claimed


def _reconcile_existing(
    *,
    settings: Settings,
    notion: NotionRepository,
    dedupe: DedupeStore,
    row,
    post: dict,
    dry: bool,
) -> None:
    """Point Notion at the post that already exists. Never creates another one."""
    state = post_state(post)
    post_id = post.get("id") or post.get("postId")
    if state == "ERROR":
        logger.error(
            "BLOCKER: Metricool post %s for %s has a provider ERROR; left as %s",
            post_id,
            row.page_id,
            row.status,
        )
        notify_slack(
            webhook_url=settings.slack_webhook_url,
            channel=settings.slack_channel,
            dedupe=dedupe,
            notion_page_id=row.page_id,
            reason="metricool_duplicate_error",
            message=(
                f"BLOCKER: existing Metricool post {post_id} for {row.url} "
                "has a provider ERROR; not created again and Notion was not changed"
            ),
            dry_run=dry,
        )
        return
    if state == "PUBLISHED":
        target = settings.notion_status_published
    else:
        # PENDING / SCHEDULED / UNKNOWN still sitting in the calendar.
        target = settings.notion_status_scheduled
    if dry:
        logger.info(
            "[dry-run] Would set Notion %s to %s (existing Metricool %s %s)",
            row.page_id,
            target,
            post_id,
            state,
        )
        return
    notion.set_status(row.page_id, target, dry_run=False)
    logger.info(
        "Existing Metricool post %s (%s) for %s; Notion set to %s",
        post_id,
        state,
        row.page_id,
        target,
    )


def run_schedule(
    *,
    settings: Settings,
    dry_run: bool | None = None,
    only_publication_date: date | None = None,
    only_page_id: str | None = None,
    exclude_channels: frozenset[str] | None = frozenset(),
) -> dict[str, int]:
    # Validate before opening clients or running any pipeline step.
    target_page = str(UUID(only_page_id)) if only_page_id is not None else None
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {"queried": 0, "scheduled": 0, "skipped": 0, "errors": 0, "reconciled": 0}

    if not settings.enable_schedule:
        logger.warning("Schedule job disabled (ENABLE_SCHEDULE=false). Exiting.")
        return stats

    logger.info("metricool_sync_posts build: %s", build_label())

    notion = NotionRepository(settings)
    metricool = MetricoolClient(settings)
    dedupe = DedupeStore(
        path=settings.slack_dedupe_file,
        ttl_seconds=settings.slack_dedupe_hours * 3600,
    )
    settings.ensure_data_dirs()

    now = now_in(settings.timezone)
    week_start, week_end = calendar_week_bounds(now, settings.timezone)
    # Whole week, then cap successful creates. Skips must not eat the fetch window.
    rows = notion.fetch_approved_current_week(week_start, week_end, limit=None)
    if target_page:
        rows = [r for r in rows if str(UUID(r.page_id)) == target_page]
        if len(rows) != 1:
            metricool.close()
            raise ValueError("--only-page-id must match exactly one approved row this week")
    if only_publication_date is not None:
        tz = settings.timezone
        rows = [r for r in rows if _publication_on_date(r, tz, only_publication_date)]
    exclude = (
        settings.schedule_exclude_channels_set()
        | (exclude_channels or frozenset())
        | _ALWAYS_EXCLUDED
    )
    if exclude:
        before = len(rows)
        kept = []
        dropped: list[str] = []
        for r in rows:
            if _row_should_drop(r, exclude):
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
    if target_page and len(rows) != 1:
        metricool.close()
        raise ValueError("--only-page-id row does not meet date/channel filters")
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

    guard = ScheduleGuard(settings.media_work_dir.parent / "schedule-guard.sqlite3")
    # --only-date / --only-page-id still schedule the filtered set; the default
    # run stops after SCHEDULE_MAX_PER_RUN successful creates.
    cap: int | None
    if only_publication_date is not None or target_page is not None:
        cap = None
    else:
        cap = settings.schedule_max_per_run

    existing_posts: list[dict] = []
    if rows:
        try:
            raw_posts = metricool.get_scheduled_posts(
                week_start - timedelta(days=7),
                week_end + timedelta(days=7),
                extended_range=True,
            )
        except Exception:
            logger.exception("Metricool duplicate lookup failed; not creating posts this run")
            stats["errors"] += 1
            metricool.close()
            return stats
        existing_posts = _as_post_list(raw_posts)
        logger.info("Loaded %s Metricool posts for duplicate check", len(existing_posts))

    claimed = _claim_linked_posts(
        notion=notion,
        linked_rows=_linked_rows(
            notion, week_start - timedelta(days=7), week_end + timedelta(days=7)
        ),
        approved_ids={row.page_id for row in rows},
        existing_posts=existing_posts,
        exclude=exclude,
        tz_name=settings.timezone,
    )
    if claimed:
        logger.info("Metricool posts already claimed by other Notion rows: %s", sorted(claimed))

    for row in rows:
        try:
            labels = canal_labels(row)
            plan = plan_canals(labels, exclude=exclude)
            if plan.skip_nico:
                stats["skipped"] += 1
                logger.warning("Skip %s: IG Nico is not scheduled", row.page_id)
                continue
            if not plan.networks or plan.unrecognized:
                stats["skipped"] += 1
                if plan.unrecognized or not labels:
                    logger.warning(
                        "Skip %s: unknown/ambiguous channel %r",
                        row.page_id,
                        list(plan.unrecognized) or labels or None,
                    )
                else:
                    logger.warning("Skip %s: LinkedIn is not scheduled", row.page_id)
                continue
            networks = list(plan.networks)
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
            if caption and caption_has_placeholder(caption):
                stats["skipped"] += 1
                logger.warning("Skip %s: placeholder in caption", row.page_id)
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="placeholder_caption",
                    message=f"Schedule skip: caption still has a placeholder for {row.url}",
                    dry_run=dry,
                )
                continue
            if caption:
                raw_hits = find_duplicate_candidates(
                    existing_posts,
                    caption=caption,
                    title=row.title,
                    network=None,
                    networks=networks,
                    tz_name=settings.timezone,
                    notion_publication=pub,
                    media_urls=[row.final_file_url] if row.final_file_url else None,
                )
                hits = [hit for hit in raw_hits if _post_key(hit) not in claimed]
                if len(hits) > 1:
                    stats["skipped"] += 1
                    for hit in hits:
                        logger.info(
                            "Match Notion %s -> Metricool %s %s",
                            row.page_id,
                            _post_key(hit),
                            format_match_evidence(
                                hit,
                                caption=caption,
                                title=row.title,
                                tz_name=settings.timezone,
                                notion_publication=pub,
                                networks=networks,
                                media_urls=(
                                    [row.final_file_url] if row.final_file_url else None
                                ),
                            ),
                        )
                    ids = [hit.get("id") or hit.get("postId") for hit in hits]
                    logger.warning(
                        "Skip %s: ambiguous duplicate, Metricool posts %s",
                        row.page_id,
                        ids,
                    )
                    notify_slack(
                        webhook_url=settings.slack_webhook_url,
                        channel=settings.slack_channel,
                        dedupe=dedupe,
                        notion_page_id=row.page_id,
                        reason="ambiguous_duplicate",
                        message=(
                            f"Schedule skip: more than one Metricool post matches {row.url} "
                            f"({ids}); not created"
                        ),
                        dry_run=dry,
                    )
                    continue
                if len(hits) == 1:
                    chosen = hits[0]
                    logger.info(
                        "Match Notion %s -> Metricool %s %s",
                        row.page_id,
                        _post_key(chosen),
                        format_match_evidence(
                            chosen,
                            caption=caption,
                            title=row.title,
                            tz_name=settings.timezone,
                            notion_publication=pub,
                            networks=networks,
                            media_urls=[row.final_file_url] if row.final_file_url else None,
                        ),
                    )
                    _reconcile_existing(
                        settings=settings,
                        notion=notion,
                        dedupe=dedupe,
                        row=row,
                        post=chosen,
                        dry=dry,
                    )
                    key = _post_key(chosen)
                    if key:
                        claimed[key] = row.page_id
                    stats["reconciled"] += 1
                    continue
                occupants = find_slot_occupants(
                    existing_posts,
                    network=None,
                    networks=networks,
                    tz_name=settings.timezone,
                    notion_publication=pub,
                )
                if occupants:
                    stats["skipped"] += 1
                    for occupant in occupants:
                        logger.info(
                            "Not the same piece Notion %s Metricool %s %s",
                            row.page_id,
                            _post_key(occupant),
                            format_match_evidence(
                                occupant,
                                caption=caption,
                                title=row.title,
                                tz_name=settings.timezone,
                                notion_publication=pub,
                                networks=networks,
                                media_urls=(
                                    [row.final_file_url] if row.final_file_url else None
                                ),
                            ),
                        )
                    ids = [_post_key(post) for post in occupants]
                    logger.warning(
                        "Skip %s: slot_conflict, Metricool posts %s",
                        row.page_id,
                        ids,
                    )
                    notify_slack(
                        webhook_url=settings.slack_webhook_url,
                        channel=settings.slack_channel,
                        dedupe=dedupe,
                        notion_page_id=row.page_id,
                        reason="slot_conflict",
                        message=(
                            f"Schedule skip: {row.url} shares a publication slot with "
                            f"Metricool {ids}, which is a different piece; not created"
                        ),
                        dry_run=dry,
                    )
                    continue
            now_local = now_in(settings.timezone)
            if pub < now_local:
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: publication %s is in the past; not rescheduled",
                    row.page_id,
                    pub,
                )
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="past_publication_date",
                    message=(
                        f"Schedule skip: Publicación {pub.isoformat()} is in the past "
                        f"for {row.url}; not moved to now"
                    ),
                    dry_run=dry,
                )
                continue
            if not caption:
                stats["skipped"] += 1
                logger.warning("Skip %s: empty caption", row.page_id)
                continue
            if is_miniatura_type(row.content_type):
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: content type %r is a thumbnail, not a post",
                    row.page_id,
                    row.content_type,
                )
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="miniatura_not_a_post",
                    message=(
                        f"Schedule skip: {row.content_type} is not scheduled "
                        f"as a standalone post ({row.url})"
                    ),
                    dry_run=dry,
                )
                continue
            file_ref = classify_final_file(row.final_file_url)
            if file_ref.kind == FinalFileKind.EMPTY:
                stats["skipped"] += 1
                logger.warning("Skip %s: no Archivo Final media", row.page_id)
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="missing_media",
                    message=f"Schedule skip: no media for {row.url}",
                    dry_run=dry,
                )
                continue
            if not dry and guard.contains(settings.metricool_blog_id, row.page_id):
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: prior schedule attempt; reconcile before retry", row.page_id
                )
                continue
            if cap is not None and stats["scheduled"] >= cap:
                logger.info(
                    "SCHEDULE_MAX_PER_RUN=%s reached; leaving remaining rows",
                    cap,
                )
                break

            cover = resolve_instagram_reel_cover(
                settings=settings,
                row=row,
                networks=networks,
                metricool=metricool,
                dry_run=dry,
            )
            if cover.skip_reason:
                stats["skipped"] += 1
                logger.warning("Skip %s: %s", row.page_id, cover.skip_reason)
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason=cover.skip_reason,
                    message=cover.skip_message
                    or f"Schedule skip: {cover.skip_reason} for {row.url}",
                    dry_run=dry,
                )
                continue

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

            if not media_urls:
                stats["skipped"] += 1
                logger.warning("Skip %s: media resolved to nothing", row.page_id)
                notify_slack(
                    webhook_url=settings.slack_webhook_url,
                    channel=settings.slack_channel,
                    dedupe=dedupe,
                    notion_page_id=row.page_id,
                    reason="missing_media",
                    message=f"Schedule skip: no media for {row.url}",
                    dry_run=dry,
                )
                continue

            body = build_schedule_body(
                caption=caption,
                publication=pub,
                tz_name=settings.timezone,
                channel=row.channel,
                title=row.title,
                content_type=row.content_type,
                media_urls=media_urls,
                cover_url=cover.cover_url,
                youtube_existing_video=youtube_existing,
                networks=networks,
                youtube_short=plan.youtube_short,
            )
            if dry:
                logger.info("[dry-run] Would schedule Metricool post for %s", row.page_id)
            else:
                # Reserve BEFORE POST: timeout/Notion failure must never auto-resend.
                if not guard.reserve(settings.metricool_blog_id, row.page_id):
                    stats["skipped"] += 1
                    continue
                metricool.create_scheduled_post(body)
                # Status flips only after the create call returns. A failed create
                # raises above and leaves Notion on Aprobado.
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
