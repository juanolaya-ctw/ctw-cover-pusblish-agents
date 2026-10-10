"""Instagram Reel / Trial Reel cover gate used by the schedule job."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.attach import (
    resolve_cover_url_for_metricool,
    resolve_miniatura_url,
)
from metricool_sync_posts.cover.bridge import prepare_cover_for_publish_task
from metricool_sync_posts.jobs.common import publication_dt
from metricool_sync_posts.jobs.content_types import infer_instagram_type
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.urls import is_dropbox_url, is_google_drive_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)

_REEL_TYPES = frozenset({"REEL", "TRIAL_REEL"})
_AGENT_REASONS = frozenset(
    {
        "missing_frame",
        "unreadable_folder",
        "unsupported_link",
        "render_failed",
        "error",
    }
)


@dataclass(frozen=True)
class ReelCover:
    """``skip_reason`` set means the row must not be scheduled."""

    cover_url: str | None = None
    skip_reason: str | None = None
    skip_message: str | None = None


def instagram_reel(networks: list[str], title: str | None, content_type: str | None) -> bool:
    return (
        "instagram" in networks
        and infer_instagram_type(title or "", content_type) in _REEL_TYPES
    )


def _cover_text(row) -> str:
    return (getattr(row, "cover_text", "") or "").strip()


def _log(page_id: str, status: str, source: str | None, reason: str | None) -> None:
    logger.info(
        "Cover page=%s status=%s source=%s reason=%s",
        page_id,
        status,
        source or "-",
        reason or "-",
    )


def _source(row, result: dict | None, *, existing: bool) -> str:
    if existing:
        return "existing"
    if isinstance(result, dict):
        raw = str(result.get("source") or "").strip().lower()
        if raw in {"dropbox", "drive", "existing"}:
            return raw
        link = result.get("cover_url")
        if isinstance(link, str) and is_dropbox_url(link):
            return "dropbox"
    archivo = getattr(row, "final_file_url", "") or ""
    if is_google_drive_url(archivo):
        return "drive"
    if is_dropbox_url(archivo):
        return "dropbox"
    return "drive"


def resolve_instagram_reel_cover(
    *,
    settings: Settings,
    row,
    networks: list[str],
    metricool: MetricoolClient,
    dry_run: bool,
) -> ReelCover:
    """Resolve videoThumbnailUrl for an Instagram reel, or a skip reason.

    Non-reels return an empty decision and are scheduled as before.
    """
    if not instagram_reel(networks, getattr(row, "title", ""), getattr(row, "content_type", None)):
        return ReelCover()

    page_id = row.page_id
    text = _cover_text(row)
    require = settings.require_cover_for_schedule
    page_url = getattr(row, "url", page_id)

    if not text:
        _log(page_id, "skipped" if require else "optional", "-", "missing_hook")
        if require:
            return ReelCover(
                skip_reason="missing_hook",
                skip_message=(
                    f"Schedule skip: Instagram reel has no Titulo hook for {page_url}"
                ),
            )
        return ReelCover()

    miniatura = getattr(row, "miniatura_url", None)
    if miniatura:
        existing = resolve_miniatura_url(
            miniatura,
            metricool=metricool,
            dry_run=dry_run,
            settings=settings,
        )
        if existing:
            _log(page_id, "ready", "existing", "-")
            return ReelCover(cover_url=existing)

    if not (settings.ctw_cover_agent_path or "").strip():
        _log(page_id, "skipped", "-", "missing_cover")
        if require:
            return ReelCover(
                skip_reason="missing_cover",
                skip_message=f"Schedule skip: missing cover for {page_url}",
            )
        return ReelCover()

    result = prepare_cover_for_publish_task(settings, row, dry_run=dry_run)
    if not isinstance(result, dict):
        _log(page_id, "skipped", "-", "error")
        if require and not dry_run:
            return ReelCover(
                skip_reason="error",
                skip_message=f"Schedule skip: cover agent failed for {page_url}",
            )
        if dry_run:
            logger.info("[dry-run] would prepare cover (titulo=%s)", text)
        return ReelCover()

    if result.get("dry_run_skipped"):
        logger.info("[dry-run] would prepare cover (titulo=%s)", text)
        _log(page_id, "would_prepare", "-", "dry_run")
        return ReelCover()

    source = _source(row, result, existing=False)
    reason = str(result.get("reason") or "").strip() or None
    if not result.get("ready"):
        reported = reason if reason in _AGENT_REASONS else (reason or "error")
        _log(page_id, "not_ready", source, reported)
        if require:
            message = result.get("message") or f"Schedule skip: cover {reported} for {page_url}"
            return ReelCover(skip_reason=reported, skip_message=str(message))
        return ReelCover()

    raw = result.get("cover_bytes")
    cover_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray)) and raw else None
    link = result.get("cover_url") if isinstance(result.get("cover_url"), str) else None

    if dry_run:
        _log(page_id, "ready", source, "-")
        logger.info("[dry-run] Would upload cover for %s (titulo=%s)", page_id, text)
        return ReelCover()

    try:
        cover_url = resolve_cover_url_for_metricool(
            settings=settings,
            metricool=metricool,
            page_id=page_id,
            dry_run=False,
            cover_bytes=cover_bytes,
            cover_link=link,
            publication=publication_dt(row, settings.timezone),
        )
    except MediaHostError as exc:
        _log(page_id, "skipped", source, "media_host_failed")
        return ReelCover(
            skip_reason="media_host_failed",
            skip_message=f"Schedule skip: media_host_failed for {page_url}: {exc}",
        )
    if cover_url:
        _log(page_id, "ready", source, "-")
        return ReelCover(cover_url=cover_url)

    _log(page_id, "skipped", source, "missing_cover")
    if require:
        return ReelCover(
            skip_reason="missing_cover",
            skip_message=f"Schedule skip: missing cover for {page_url}",
        )
    return ReelCover()
