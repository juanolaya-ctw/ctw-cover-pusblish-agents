"""Prepare media URLs for Metricool scheduling."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.download import download_to_path
from metricool_sync_posts.media.ffmpeg_util import (
    probe_width,
    remux_mov_to_mp4,
    scale_video_max_width,
)
from metricool_sync_posts.media.upload_public import upload_public_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def extension_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    return suffix or ".bin"


def prepare_media_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    source_url: str,
    dry_run: bool,
    work_suffix: str = "",
) -> str | None:
    """
    Return a URL suitable for Metricool normalize (original or remuxed/scaled upload).
    """
    if not source_url:
        return None
    ext = extension_from_url(source_url)
    work = settings.media_work_dir
    work.mkdir(parents=True, exist_ok=True)
    local = work / f"media{work_suffix}{ext}"

    if dry_run:
        logger.info("[dry-run] Would process media from %s", source_url)
        return source_url

    download_to_path(source_url, local, settings=settings)
    processed = local

    if ext == ".mov":
        mp4 = work / "media-remux.mp4"
        remux_mov_to_mp4(settings.ffmpeg_bin, local, mp4)
        processed = mp4
        ext = ".mp4"

    if ext in VIDEO_EXT:
        width = probe_width(settings.ffprobe_bin, processed)
        if width and width > settings.instagram_video_max_width:
            scaled = work / "media-scaled.mp4"
            scale_video_max_width(
                settings.ffmpeg_bin,
                processed,
                scaled,
                settings.instagram_video_max_width,
            )
            processed = scaled

    # Metricool requires durable public URLs; Dropbox .mov may fail — upload if needed
    needs_upload = ext == ".mp4" and processed != local
    if needs_upload or not _url_is_probably_public(source_url):
        public = upload_public_url(settings, processed)
        source_url = public

    try:
        norm = metricool.normalize_media_url(source_url)
    except Exception as exc:
        logger.warning("Metricool normalize failed for %s: %s", source_url[:80], exc)
        return source_url
    if not norm:
        return source_url
    if isinstance(norm, str):
        return norm
    media_url = norm.get("url") or norm.get("normalizedUrl") or source_url
    media_id = norm.get("mediaId") or norm.get("id")
    if media_id:
        logger.info("Metricool normalized mediaId=%s", media_id)
    return media_url


def _url_is_probably_public(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if "dropbox" in host or "drive.google" in host:
        return False
    return True
