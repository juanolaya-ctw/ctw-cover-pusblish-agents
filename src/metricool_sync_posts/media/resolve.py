"""Resolve Archivo Final to one or more URLs for Metricool."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.drive_folder import (
    drive_file_metadata,
    drive_file_ref_url,
    has_drive_service_account,
    list_folder_files,
    pick_folder_entries,
)
from metricool_sync_posts.media.dropbox_host import list_shared_folder, pick_shared_entries
from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.media.urls import is_dropbox_folder_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceMedia:
    """One Archivo Final item, with Drive or Dropbox metadata when we have it."""

    url: str
    drive_file_id: str | None = None
    mime_type: str | None = None
    size: int | None = None
    dropbox_file_id: str | None = None
    dropbox_name: str | None = None


def _is_carousel(content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    return "carrusel" in ct or "carousel" in ct


def _is_stories(content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    return "historia" in ct or "stories" in ct or "story" in ct


def resolve_source_media(
    settings: Settings,
    raw_url: str | None,
    *,
    content_type: str | None,
) -> list[SourceMedia]:
    """Notion Archivo Final → media items, including Drive size and mime when known."""
    ref = classify_final_file(raw_url)
    if ref.kind == FinalFileKind.EMPTY:
        return []
    if ref.kind == FinalFileKind.YOUTUBE:
        return [SourceMedia(url=ref.raw_url)]
    if ref.kind == FinalFileKind.GOOGLE_DRIVE_FOLDER:
        assert ref.drive_folder_id
        entries = list_folder_files(settings, ref.drive_folder_id)
        picked = pick_folder_entries(
            entries,
            folder_id=ref.drive_folder_id,
            carousel=_is_carousel(content_type),
            stories=_is_stories(content_type),
        )
        return [_source_from_drive_entry(entry) for entry in picked]
    if ref.kind == FinalFileKind.GOOGLE_DRIVE_FILE:
        assert ref.drive_file_id
        meta: dict = {}
        if has_drive_service_account(settings):
            try:
                meta = drive_file_metadata(settings, ref.drive_file_id)
            except Exception as exc:
                logger.warning(
                    "Drive metadata for file %s failed (%s)",
                    ref.drive_file_id,
                    exc,
                )
        return [_source_from_drive_entry({"id": ref.drive_file_id, **meta})]
    if ref.kind == FinalFileKind.DROPBOX and is_dropbox_folder_url(ref.raw_url):
        return _dropbox_folder_sources(
            settings,
            ref.raw_url,
            carousel=_is_carousel(content_type),
            stories=_is_stories(content_type),
        )
    return [SourceMedia(url=ref.raw_url)]


def resolve_source_urls(
    settings: Settings,
    raw_url: str | None,
    *,
    content_type: str | None,
) -> list[str]:
    """Notion Archivo Final → list of HTTP URLs to feed the media pipeline."""
    return [
        item.url
        for item in resolve_source_media(settings, raw_url, content_type=content_type)
    ]


def _dropbox_folder_sources(
    settings: Settings,
    folder_url: str,
    *,
    carousel: bool,
    stories: bool,
) -> list[SourceMedia]:
    entries = list_shared_folder(settings, folder_url)
    picked = pick_shared_entries(entries, carousel=carousel, stories=stories)
    sources: list[SourceMedia] = []
    for entry in picked:
        file_id = str(entry.get("id") or "").strip()
        name = entry.get("name") if isinstance(entry.get("name"), str) else None
        sources.append(
            SourceMedia(
                url=folder_url,
                dropbox_file_id=file_id or None,
                dropbox_name=name,
                size=_size(entry.get("size")),
            )
        )
    logger.info(
        "Dropbox folder resolved to %s file(s): %s",
        len(sources),
        ", ".join(item.dropbox_name or item.dropbox_file_id or "?" for item in sources),
    )
    return sources


def _source_from_drive_entry(entry: dict) -> SourceMedia:
    file_id = str(entry.get("id") or "")
    mime = entry.get("mimeType") if isinstance(entry.get("mimeType"), str) else None
    name = entry.get("name") if isinstance(entry.get("name"), str) else None
    size = _size(entry.get("size"))
    return SourceMedia(
        url=drive_file_ref_url(file_id, name, mime),
        drive_file_id=file_id or None,
        mime_type=mime,
        size=size,
    )


def _size(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def prepare_media_urls_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    raw_archivo_final: str | None,
    content_type: str | None,
    dry_run: bool,
    notion_page_id: str | None = None,
    publication: datetime | None = None,
    networks: list[str] | None = None,
) -> list[str]:
    """Download/normalize each resolved URL; return Metricool-ready media URLs."""
    ref = classify_final_file(raw_archivo_final)
    if ref.kind == FinalFileKind.EMPTY:
        return []
    if ref.kind == FinalFileKind.YOUTUBE:
        return [ref.raw_url]

    # Dry-run: avoid Drive SA listing/download; still exercise exclude + caption path.
    if dry_run and ref.kind in (
        FinalFileKind.GOOGLE_DRIVE_FOLDER,
        FinalFileKind.GOOGLE_DRIVE_FILE,
    ):
        logger.info(
            "[dry-run] Would resolve/download Drive %s via service account API: %s",
            ref.kind.value,
            (raw_archivo_final or "")[:120],
        )
        return [ref.raw_url or "https://drive.google.com/dry-run"]
    if dry_run and ref.kind == FinalFileKind.DROPBOX and is_dropbox_folder_url(ref.raw_url):
        logger.info(
            "[dry-run] Would list Dropbox folder and upload the inner file: %s",
            ref.raw_url[:120],
        )
        return [ref.raw_url]

    sources = resolve_source_media(settings, raw_archivo_final, content_type=content_type)
    if not sources:
        return []

    out: list[str] = []
    for idx, src in enumerate(sources, start=1):
        url = prepare_media_for_metricool(
            settings=settings,
            metricool=metricool,
            source_url=src.url,
            dry_run=dry_run,
            work_suffix=f"-{idx}" if idx else "",
            notion_page_id=notion_page_id,
            index=idx,
            publication=publication,
            drive_mime=src.mime_type,
            drive_size=src.size,
            drive_file_id=src.drive_file_id,
            dropbox_file_id=src.dropbox_file_id,
            dropbox_name=src.dropbox_name,
            networks=networks,
        )
        if url:
            out.append(url)
    return out
