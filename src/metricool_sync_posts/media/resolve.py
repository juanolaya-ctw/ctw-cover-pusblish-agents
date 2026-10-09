"""Resolve Archivo Final to one or more URLs for Metricool."""

from __future__ import annotations

import logging

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.drive_folder import resolve_folder_to_download_urls
from metricool_sync_posts.media.final_file import FinalFileKind, classify_final_file
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.media.urls import google_drive_direct_url
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
        return [google_drive_direct_url(ref.raw_url)]
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
    sources = resolve_source_urls(settings, raw_archivo_final, content_type=content_type)
    if not sources:
        return []

    ref = classify_final_file(raw_archivo_final)
    if ref.kind == FinalFileKind.YOUTUBE:
        return sources

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
