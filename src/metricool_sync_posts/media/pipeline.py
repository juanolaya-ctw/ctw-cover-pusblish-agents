"""Prepare media URLs for Metricool scheduling.

Every image and video is uploaded into Metricool planner storage. The post
stores the ``static.metricool.com`` URL from that upload. External hosts
(Drive, Dropbox, litterbox, uguu, transfer.sh) are not sent to Metricool.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.download import download_to_path
from metricool_sync_posts.media.dropbox_host import download_shared_file
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.ffmpeg_util import (
    IG_MAX_BYTES,
    instagram_reencode_reason,
    probe_video,
    reencode_instagram_video,
)
from metricool_sync_posts.media.filetype import MediaType, ensure_media_suffix
from metricool_sync_posts.media.urls import is_dropbox_folder_url
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.media_upload import is_metricool_static_url

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ZIP_MAGIC = b"PK\x03\x04"


def extension_from_url(url: str) -> str:
    """Suffix from the path, or from a ``#png`` hint on extension-less hosts.

    The hint is not sent to Drive or Metricool. ``build_schedule_body`` strips
    it before the create call. Unknown URLs still report ``.bin`` so callers
    can tell the type was not detected; uploads must not keep that name.
    """
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    known = IMAGE_EXT | VIDEO_EXT
    if suffix in known:
        return suffix
    frag = parsed.fragment.lower().strip()
    if frag and not frag.startswith("."):
        frag = f".{frag}"
    if frag in known:
        return frag
    return suffix or ".bin"


def with_extension_hint(url: str, ext: str) -> str:
    """Mark an extension-less URL so image/video routing still works."""
    suffix = ext if ext.startswith(".") else f".{ext}"
    if suffix not in IMAGE_EXT | VIDEO_EXT:
        return url
    parsed = urlparse(url)
    if Path(parsed.path).suffix.lower() in IMAGE_EXT | VIDEO_EXT:
        return url
    return urlunparse(parsed._replace(fragment=suffix.lstrip(".")))


def strip_extension_hint(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.fragment:
        return url
    return urlunparse(parsed._replace(fragment=""))


def prepare_media_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    source_url: str,
    dry_run: bool,
    work_suffix: str = "",
    notion_page_id: str | None = None,
    index: int = 1,
    publication: datetime | None = None,
    drive_mime: str | None = None,
    drive_size: int | None = None,
    drive_file_id: str | None = None,
    dropbox_file_id: str | None = None,
    dropbox_name: str | None = None,
    networks: list[str] | None = None,
) -> str | None:
    """Download the source file and return its Metricool static URL."""
    del notion_page_id, index, publication, drive_size, drive_file_id
    if not source_url:
        return None
    if dry_run:
        logger.info("[dry-run] Would process media from %s", source_url)
        return source_url
    if is_metricool_static_url(source_url):
        logger.info("Media already on static.metricool.com")
        return strip_extension_hint(source_url)
    if is_dropbox_folder_url(source_url) and not dropbox_file_id:
        raise MediaHostError(
            "Dropbox folder link was not resolved to a file. "
            "A /scl/fo/ URL with dl=1 is a ZIP, not the video."
        )

    work = settings.media_work_dir
    work.mkdir(parents=True, exist_ok=True)
    ext = extension_from_url(dropbox_name or source_url)
    local = work / f"media{work_suffix}{ext if ext != '.bin' else '.bin'}"
    if dropbox_file_id:
        download_shared_file(settings, dropbox_file_id, local)
    else:
        download_to_path(source_url, local, settings=settings)
    _reject_zip(local)

    local, detected = ensure_media_suffix(local, mime=drive_mime)
    processed = local
    for_instagram = any(network.lower() == "instagram" for network in (networks or []))
    if for_instagram and detected.is_video:
        processed = apply_instagram_video_limits(
            settings,
            local,
            max_width=settings.instagram_video_max_width,
        )
        detected = MediaType(".mp4", "video/mp4") if processed != local else detected

    try:
        url = metricool.upload_planner_media(
            processed.read_bytes(),
            content_type=detected.content_type,
            file_extension=detected.ext.lstrip("."),
        )
    except MediaHostError:
        raise
    except Exception as exc:
        raise MediaHostError(f"Metricool media upload failed: {exc}") from exc
    if not isinstance(url, str) or not url.strip():
        raise MediaHostError("Metricool media upload returned no URL")
    return url.strip()


def apply_instagram_video_limits(settings: Settings, path: Path, *, max_width: int) -> Path:
    """Re-encode only when Instagram would reject the file. Other networks keep it."""
    width, bitrate = probe_video(settings.ffprobe_bin, path)
    reason = instagram_reencode_reason(
        size=path.stat().st_size,
        bitrate=bitrate,
        width=width,
        max_width=max_width,
    )
    if reason is None:
        if bitrate is None and path.stat().st_size <= IG_MAX_BYTES:
            logger.info(
                "Instagram video %s: bitrate unknown and size is under 300 MB; keeping the file",
                path.name,
            )
        return path
    dest = path.with_name(f"{path.stem}-ig.mp4")
    scale = max_width if reason == "width" or (width is not None and width > max_width) else None
    logger.info(
        "Re-encoding %s for Instagram (%s, bitrate=%s size=%s)",
        path.name,
        reason,
        bitrate,
        path.stat().st_size,
    )
    try:
        return reencode_instagram_video(
            settings.ffmpeg_bin,
            path,
            dest,
            scale_width=scale,
        )
    except RuntimeError as exc:
        raise MediaHostError(str(exc)) from exc


def _reject_zip(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(4)
    if header == _ZIP_MAGIC:
        raise MediaHostError(
            f"{path.name} is a ZIP. A Dropbox folder link downloads as a ZIP, not the video. "
            "Resolve the file inside the folder and upload that."
        )
