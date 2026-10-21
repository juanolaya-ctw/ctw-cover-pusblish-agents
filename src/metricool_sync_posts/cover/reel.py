"""Instagram Reel / Trial Reel cover gate used by the schedule job."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.attach import (
    resolve_cover_url_for_metricool,
    resolve_miniatura_url,
)
from metricool_sync_posts.cover.bridge import (
    prepare_cover_for_publish_task,
    publish_task_from_row,
    render_cover_on_local_frame,
)
from metricool_sync_posts.jobs.common import publication_dt
from metricool_sync_posts.jobs.content_types import infer_instagram_type
from metricool_sync_posts.media.dropbox_host import (
    FILE_NOT_FOUND_REASON,
    NO_COVER_IN_FOLDER,
    download_shared_entry,
    find_shared_cover,
)
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.urls import (
    is_dropbox_folder_url,
    is_dropbox_url,
    is_google_drive_url,
)
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

    archivo = getattr(row, "final_file_url", "") or ""
    dropbox_still: dict | None = None
    if is_dropbox_folder_url(archivo):
        if dry_run:
            logger.info(
                "[dry-run] Would look up the reel still in the Dropbox shared folder for %s",
                page_id,
            )
            if not (settings.ctw_cover_agent_path or "").strip():
                _log(page_id, "would_prepare", "dropbox", "dry_run")
                return ReelCover()
        else:
            located = _locate_dropbox_still(settings, archivo, page_id)
            if isinstance(located, ReelCover):
                return located
            if isinstance(located, dict):
                dropbox_still = located

    if not (settings.ctw_cover_agent_path or "").strip():
        if dropbox_still is not None:
            return _cover_from_dropbox_still(
                settings=settings,
                metricool=metricool,
                row=row,
                page_id=page_id,
                page_url=page_url,
                archivo=archivo,
                entry=dropbox_still,
                titulo=text,
                dry_run=dry_run,
            )
        _log(page_id, "skipped", "-", "missing_cover")
        if require:
            return ReelCover(
                skip_reason="missing_cover",
                skip_message=f"Schedule skip: missing cover for {page_url}",
            )
        return ReelCover()

    result = prepare_cover_for_publish_task(settings, row, dry_run=dry_run)
    if isinstance(result, dict) and result.get("dry_run_skipped"):
        logger.info("[dry-run] would prepare cover (titulo=%s)", text)
        _log(page_id, "would_prepare", "-", "dry_run")
        return ReelCover()

    source = _source(row, result if isinstance(result, dict) else None, existing=False)
    if not (isinstance(result, dict) and result.get("ready")):
        reason, detail = _unready_reason(result)
        _log(page_id, "not_ready", source, reason)
        logger.info(
            "prepare_cover_for_publish_task not ready page=%s reason=%s message=%s",
            page_id,
            reason,
            detail,
        )
        if dropbox_still is not None and not dry_run:
            return _cover_from_dropbox_still(
                settings=settings,
                metricool=metricool,
                row=row,
                page_id=page_id,
                page_url=page_url,
                archivo=archivo,
                entry=dropbox_still,
                titulo=text,
                dry_run=False,
            )
        if not isinstance(result, dict):
            if require and not dry_run:
                return ReelCover(
                    skip_reason="error",
                    skip_message=f"Schedule skip: cover agent failed for {page_url}",
                )
            if dry_run:
                logger.info("[dry-run] would prepare cover (titulo=%s)", text)
            return ReelCover()
        reported = reason if reason in _AGENT_REASONS else (reason or "error")
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


def _locate_dropbox_still(
    settings: Settings,
    archivo: str,
    page_id: str,
) -> ReelCover | dict | None:
    """The still entry, a skip decision, or None to keep the normal cover path.

    A missing image with covers optional does not skip: the cover agent can
    still run. ``sharing.read`` and a required still that is not in the folder
    are their own skip reasons, not ``missing_cover``.
    """
    try:
        image = find_shared_cover(settings, archivo)
    except MediaHostError as exc:
        _log(page_id, "skipped", "dropbox", exc.reason)
        return ReelCover(skip_reason=exc.reason, skip_message=str(exc))
    if image is None:
        if not settings.require_cover_for_schedule:
            return None
        _log(page_id, "skipped", "dropbox", FILE_NOT_FOUND_REASON)
        return ReelCover(skip_reason=FILE_NOT_FOUND_REASON, skip_message=NO_COVER_IN_FOLDER)
    return image


def _unready_reason(result: dict | None) -> tuple[str, str]:
    """Why ``prepare_cover_for_publish_task`` did not return a ready cover."""
    if not isinstance(result, dict):
        return "no_result", "prepare_cover_for_publish_task returned no result"
    reason = str(result.get("reason") or "").strip() or "error"
    message = str(result.get("message") or "").strip() or "-"
    return reason, message


def _render_skip_message(detail: str) -> str:
    text = (
        "No se armó la portada del Reel: el Titulo no quedó dibujado sobre el "
        "frame de Dropbox, así que no se publica el frame sin el hook."
    )
    if detail and detail != "-":
        return f"{text} Motivo: {detail}"
    return text


def _cover_from_dropbox_still(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    row,
    page_id: str,
    page_url: str,
    archivo: str,
    entry: dict,
    titulo: str,
    dry_run: bool,
) -> ReelCover:
    """Download the still and render Titulo over it. Never upload the raw frame."""
    if dry_run:
        _log(page_id, "would_prepare", "dropbox", "dry_run")
        logger.info(
            "[dry-run] would render Titulo over the Dropbox still (titulo=%s)",
            titulo,
        )
        return ReelCover()
    name = str(entry.get("name") or "cover.png")
    safe = Path(name).name
    if safe in {"", ".", ".."}:
        safe = "cover.png"
    dest = settings.media_work_dir / "dropbox-cover" / safe
    try:
        download_shared_entry(settings, archivo, entry, dest)
    except MediaHostError as exc:
        _log(page_id, "skipped", "dropbox", exc.reason)
        return ReelCover(skip_reason=exc.reason, skip_message=str(exc))
    try:
        still_bytes = dest.read_bytes()
    except OSError as exc:
        return ReelCover(
            skip_reason="media_host_failed",
            skip_message=f"Schedule skip: media_host_failed for {page_url}: {exc}",
        )
    rendered = render_cover_on_local_frame(
        settings,
        titulo=titulo,
        frame_bytes=still_bytes,
        frame_path=dest,
        dry_run=False,
        task=publish_task_from_row(row),
    )
    cover_bytes, fail_reason, fail_detail = _rendered_png(rendered, still_bytes)
    if cover_bytes is None:
        _log(page_id, "skipped", "dropbox", fail_reason)
        if settings.require_cover_for_schedule:
            return ReelCover(
                skip_reason=fail_reason,
                skip_message=_render_skip_message(fail_detail),
            )
        return ReelCover()
    try:
        cover_url = resolve_cover_url_for_metricool(
            settings=settings,
            metricool=metricool,
            page_id=page_id,
            dry_run=False,
            cover_bytes=cover_bytes,
            publication=None,
        )
    except MediaHostError as exc:
        _log(page_id, "skipped", "dropbox", "media_host_failed")
        return ReelCover(
            skip_reason="media_host_failed",
            skip_message=f"Schedule skip: media_host_failed for {page_url}: {exc}",
        )
    if cover_url:
        _log(page_id, "ready", "dropbox", "-")
        return ReelCover(cover_url=cover_url)
    _log(page_id, "skipped", "dropbox", "missing_cover")
    if settings.require_cover_for_schedule:
        return ReelCover(
            skip_reason="missing_cover",
            skip_message=_render_skip_message("la portada renderizada no se pudo subir"),
        )
    return ReelCover()


def _rendered_png(
    rendered: dict | None,
    still_bytes: bytes,
) -> tuple[bytes | None, str, str]:
    """Designed cover bytes, or a skip reason when the still would go out raw."""
    if not isinstance(rendered, dict) or not rendered.get("ready"):
        reason = "render_failed"
        detail = "no se pudo renderizar el Titulo sobre el frame"
        if isinstance(rendered, dict):
            raw_reason = str(rendered.get("reason") or "").strip()
            if raw_reason:
                reason = raw_reason if raw_reason in _AGENT_REASONS else "render_failed"
            detail = str(rendered.get("message") or "").strip() or detail
        return None, reason, detail
    raw = rendered.get("cover_bytes")
    if isinstance(raw, (bytes, bytearray)) and raw:
        cover = bytes(raw)
        if cover == still_bytes:
            return None, "render_failed", "el render devolvió el frame sin el Titulo"
        return cover, "", ""
    link = rendered.get("cover_url")
    if isinstance(link, str) and link.strip():
        return None, "render_failed", "la portada no vino en bytes; no se usa el frame crudo"
    return None, "render_failed", "el render no devolvió la portada"
