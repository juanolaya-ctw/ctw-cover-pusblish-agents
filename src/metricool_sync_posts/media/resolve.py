"""Resolve Archivo Final to one or more URLs for Metricool."""

from __future__ import annotations

import logging

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.drive_folder import (
    drive_file_ref_url,
    resolve_folder_to_download_urls,
)
from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)


def _is_carousel(content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    return "carrusel" in ct or "carousel" in ct


def _is_stories(content_type: str | None) -> bool:
    ct = (content_type or "").lower()
    return "historia" in ct or "stories" in ct or "story" in ct


def resolve_source_urls(
    settings: Settings,
    raw_url: str | None,
    *,
    content_type: str | None,
) -> list[str]:
    """Notion Archivo Final → list of HTTP URLs to feed the media pipeline."""
    ref = classify_final_file(raw_url)
    if ref.kind == FinalFileKind.EMPTY:
        return []
    if ref.kind == FinalFileKind.YOUTUBE:
        return [ref.raw_url]
    if ref.kind == FinalFileKind.GOOGLE_DRIVE_FOLDER:
        assert ref.drive_folder_id
        return resolve_folder_to_download_urls(
            settings,
            ref.drive_folder_id,
            carousel=_is_carousel(content_type),
            stories=_is_stories(content_type),
        )
    if ref.kind == FinalFileKind.GOOGLE_DRIVE_FILE:
        # Keep a /file/d/{id}/… URL so download uses SA API (alt=media), not uc?export.
        assert ref.drive_file_id
        return [drive_file_ref_url(ref.drive_file_id)]
    return [ref.raw_url]


def prepare_media_urls_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    raw_archivo_final: str | None,
    content_type: str | None,
    dry_run: bool,
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

    sources = resolve_source_urls(settings, raw_archivo_final, content_type=content_type)
    if not sources:
        return []

    out: list[str] = []
    for idx, src in enumerate(sources):
        url = prepare_media_for_metricool(
            settings=settings,
            metricool=metricool,
            source_url=src,
            dry_run=dry_run,
            work_suffix=f"-{idx}" if idx else "",
        )
        if url:
            out.append(url)
    return out
