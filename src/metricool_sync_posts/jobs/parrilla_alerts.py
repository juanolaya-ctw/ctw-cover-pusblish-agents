"""Slack alerts for Programado pieces the sync did not create in Metricool.

``programado_sin_metricool`` repeats with the 24h dedupe. ``reel_manual_sin_portada``
is once per Metricool post. Neither alert changes Notion or edits the post.
"""

from __future__ import annotations

import logging
from datetime import datetime

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.common import caption_for_row, publication_dt
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard
from metricool_sync_posts.metricool.channels import canal_labels, plan_canals
from metricool_sync_posts.metricool.matching import (
    find_best_match,
    find_duplicate_candidates,
    find_sync_match,
    is_instagram_reel_post,
    post_has_cover,
    post_numeric_id,
    post_public_link,
    post_publication_datetime,
    post_stable_uuid,
    same_metricool_post,
)
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.digest import SlackDigest
from metricool_sync_posts.slack.notify import notify_slack

logger = logging.getLogger(__name__)

PROGRAMADO_SIN_METRICOOL = "programado_sin_metricool"
REEL_MANUAL_SIN_PORTADA = "reel_manual_sin_portada"


def piece_label(row) -> str:
    """Slack mrkdwn link to the Notion page, named by its title."""
    url = getattr(row, "url", None) or ""
    title = (getattr(row, "title", None) or "").strip() or "Pieza sin nombre"
    title = title.replace("|", "/").replace(">", "›").replace("<", "‹")
    if url:
        return f"<{url}|{title}>"
    return f"{title} ({row.page_id})"


def _fmt_local(dt: datetime) -> str:
    return dt.strftime("%d/%m %I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m.")


def programado_message(row) -> str:
    return (
        f"{piece_label(row)}: está en Programado y no hay ningún post en Metricool "
        "que coincida con esta pieza."
    )


def reel_message(row, post: dict, tz_name: str) -> str:
    post_id = post_numeric_id(post) or "sin id"
    when = post_publication_datetime(post, tz_name)
    when_txt = _fmt_local(when) if when is not None else "sin fecha"
    detail = f"post {post_id}, {when_txt}"
    link = post_public_link(post)
    if link:
        detail = f"{detail}, {link}"
    return (
        f"{piece_label(row)}: Reel de Instagram subido a mano en Metricool, sin portada "
        f"({detail})."
    )


def _remember(found: list[dict], post: dict | None) -> None:
    if not post:
        return
    if any(same_metricool_post(post, item) for item in found):
        return
    found.append(post)


def matching_posts(
    row,
    posts: list[dict],
    *,
    tz_name: str,
    caption: str,
    window_days: int,
) -> list[dict]:
    """Posts confirm or schedule would treat as this Notion piece."""
    pub = publication_dt(row, tz_name)
    found: list[dict] = []
    stored_id = str(getattr(row, "metricool_id", None) or "").strip() or None
    stored_uuid = str(getattr(row, "metricool_uuid", None) or "").strip() or None
    channel = getattr(row, "channel", None)
    if pub is not None:
        _remember(
            found,
            find_best_match(
                notion_caption=caption,
                notion_channel=channel,
                notion_publication=pub,
                candidates=posts,
                tz_name=tz_name,
            ),
        )
    _remember(
        found,
        find_sync_match(
            notion_caption=caption,
            notion_title=getattr(row, "title", None),
            notion_channel=channel,
            notion_publication=pub,
            candidates=posts,
            tz_name=tz_name,
            metricool_id=stored_id,
            metricool_uuid=stored_uuid,
            window_days=window_days,
        ),
    )
    plan = plan_canals(canal_labels(row))
    for hit in find_duplicate_candidates(
        posts,
        caption=caption,
        title=getattr(row, "title", None),
        network=None,
        networks=list(plan.networks) or None,
        tz_name=tz_name,
        notion_publication=pub,
        media_urls=(
            [row.final_file_url] if getattr(row, "final_file_url", None) else None
        ),
        window_days=window_days,
    ):
        _remember(found, hit)
    return found


def _caption(notion, row) -> str:
    try:
        text = caption_for_row(notion, row)
    except Exception:
        logger.exception("Caption failed for %s", getattr(row, "page_id", "?"))
        text = getattr(row, "caption", "") or ""
    return text.strip() if isinstance(text, str) else ""


def notify_programado_sin_metricool(
    *,
    settings: Settings,
    dedupe: DedupeStore,
    digest: SlackDigest | None,
    row,
    dry: bool,
) -> None:
    """24h reminder. Does not change Estado."""
    logger.info("No Metricool match for Notion page %s", row.page_id)
    notify_slack(
        webhook_url=settings.slack_webhook_url,
        channel=settings.slack_channel,
        dedupe=dedupe,
        notion_page_id=row.page_id,
        reason=PROGRAMADO_SIN_METRICOOL,
        message=programado_message(row),
        dry_run=dry,
        digest=digest,
        title=getattr(row, "title", None),
        url=getattr(row, "url", None),
    )


def _reel_dedupe_id(post: dict) -> str:
    pid = post_numeric_id(post)
    if pid:
        return f"metricool-post-{pid}"
    uid = post_stable_uuid(post)
    if uid:
        return f"metricool-uuid-{uid}"
    return ""


def alert_manual_reels(
    *,
    settings: Settings,
    dedupe: DedupeStore,
    digest: SlackDigest | None,
    guard: ScheduleGuard,
    row,
    posts: list[dict],
    dry: bool,
) -> None:
    """Once per post, and only when the sync has no guard row for this page.

    A guard row means the sync reserved the piece before creating it. No row
    means the Reel was uploaded by hand. The Metricool post is not edited.
    """
    page_id = getattr(row, "page_id", None)
    if not page_id or guard.seen(settings.metricool_blog_id, page_id):
        return
    for post in posts:
        if not is_instagram_reel_post(post) or post_has_cover(post):
            continue
        notify_slack(
            webhook_url=settings.slack_webhook_url,
            channel=settings.slack_channel,
            dedupe=dedupe,
            notion_page_id=page_id,
            reason=REEL_MANUAL_SIN_PORTADA,
            message=reel_message(row, post, settings.timezone),
            dry_run=dry,
            digest=digest,
            title=getattr(row, "title", None),
            url=getattr(row, "url", None),
            once=True,
            dedupe_id=_reel_dedupe_id(post) or None,
        )


def review_programado_gaps(
    *,
    settings: Settings,
    notion,
    dedupe: DedupeStore,
    digest: SlackDigest | None,
    guard: ScheduleGuard,
    rows: list,
    posts: list[dict],
    window_start: datetime,
    window_end: datetime,
    dry: bool,
) -> None:
    """Programado rows in the schedule window with no Metricool match, plus manual reels."""
    scheduled = settings.notion_status_scheduled
    window_days = settings.publication_window_days
    for row in rows:
        page_id = getattr(row, "page_id", None)
        if not page_id:
            continue
        try:
            pub = publication_dt(row, settings.timezone)
            caption = _caption(notion, row)
            hits = matching_posts(
                row,
                posts,
                tz_name=settings.timezone,
                caption=caption,
                window_days=window_days,
            )
            if hits:
                alert_manual_reels(
                    settings=settings,
                    dedupe=dedupe,
                    digest=digest,
                    guard=guard,
                    row=row,
                    posts=hits,
                    dry=dry,
                )
                continue
            status = (getattr(row, "status", None) or "").strip()
            if status != scheduled or pub is None:
                continue
            if pub < window_start or pub > window_end:
                continue
            notify_programado_sin_metricool(
                settings=settings,
                dedupe=dedupe,
                digest=digest,
                row=row,
                dry=dry,
            )
        except Exception:
            logger.exception("Programado review failed for %s", page_id)
