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
from metricool_sync_posts.media.urls import (
    dropbox_direct_url,
    is_dropbox_url,
    is_google_drive_url,
)
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

    # Dropbox (non-.mov): Metricool can usually fetch dl=1 / raw=1 — try before re-host.
    if is_dropbox_url(source_url) and ext != ".mov":
        public_dropbox = dropbox_direct_url(source_url)
        try:
            norm = metricool.normalize_media_url(public_dropbox)
            media_url = _norm_url(norm, public_dropbox)
            if media_url:
                logger.info(
                    "Pass-through Dropbox URL to Metricool normalize (no re-host): %s",
                    public_dropbox[:100],
                )
                return media_url
        except Exception as exc:
            logger.warning(
                "Dropbox pass-through normalize failed (%s); will download + re-host",
                exc,
            )

    # Drive/private sources: download locally (SA alt=media when configured).
    download_to_path(source_url, local, settings=settings)
    processed = local

    if ext == ".mov":
        mp4 = work / f"media{work_suffix}-remux.mp4"
        remuxed = remux_mov_to_mp4(settings.ffmpeg_bin, local, mp4)
        if remuxed is not None:
            processed = remuxed
            ext = ".mp4"
        else:
            logger.warning(
                "Keeping .mov without remux (ffmpeg missing). "
                "For YouTube long video prefer: winget install --id Gyan.FFmpeg -e"
            )

    if ext in VIDEO_EXT and processed.suffix.lower() in {".mp4", ".m4v", ".webm"}:
        width = probe_width(settings.ffprobe_bin, processed)
        if width and width > settings.instagram_video_max_width:
            scaled = work / f"media{work_suffix}-scaled.mp4"
            scaled_out = scale_video_max_width(
                settings.ffmpeg_bin,
                processed,
                scaled,
                settings.instagram_video_max_width,
            )
            if scaled_out is not None:
                processed = scaled_out

    # Re-host when local bytes differ from a fetchable public URL (Drive always).
    must_rehost = is_google_drive_url(source_url) or processed != local
    if must_rehost or not _url_is_pass_through(source_url):
        source_url = upload_public_url(settings, processed)
    elif is_dropbox_url(source_url):
        source_url = dropbox_direct_url(source_url)

    try:
        norm = metricool.normalize_media_url(source_url)
    except Exception as exc:
        logger.warning("Metricool normalize failed for %s: %s", source_url[:80], exc)
        return source_url
    return _norm_url(norm, source_url) or source_url


def _norm_url(norm: dict | str | None, fallback: str) -> str | None:
    if not norm:
        return None
    if isinstance(norm, str):
        return norm
    media_url = norm.get("url") or norm.get("normalizedUrl") or fallback
    media_id = norm.get("mediaId") or norm.get("id")
    if media_id:
        logger.info("Metricool normalized mediaId=%s", media_id)
    return media_url if isinstance(media_url, str) else fallback


def _url_is_pass_through(url: str) -> bool:
    """True when Metricool can fetch the URL without re-hosting local bytes."""
    host = urlparse(url).netloc.lower()
    if "drive.google" in host or "docs.google" in host:
        return False
    if "dropbox.com" in host or "dropboxusercontent.com" in host:
        return True
    if "youtube.com" in host or "youtu.be" in host:
        return True
    # Already on a public CDN / temp host
    return not ("localhost" in host or host.startswith("127."))
