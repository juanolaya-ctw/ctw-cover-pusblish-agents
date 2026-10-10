"""Turn a reel cover into a Metricool static JPEG URL.

ScheduledPost has no cover field on instagramData. The cover is the top-level
``videoThumbnailUrl``. The agent writes ``cover-final.png``; that PNG is
converted to JPEG and uploaded with the planner transaction. An external
Dropbox or Drive URL is not stored: the Metricool UI only renders
``static.metricool.com``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.ffmpeg_util import png_to_jpeg
from metricool_sync_posts.media.filetype import sniff_header
from metricool_sync_posts.media.urls import dropbox_direct_url
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.media_upload import is_metricool_static_url

logger = logging.getLogger(__name__)

_JPEG = b"\xff\xd8\xff"


def host_cover_jpeg(metricool: MetricoolClient, jpeg: bytes) -> str:
    """Upload JPEG bytes and return the Metricool URL (``videoThumbnailUrl``)."""
    if not jpeg.startswith(_JPEG):
        raise MediaHostError("Reel cover must be a JPEG after converting cover-final.png")
    try:
        url = metricool.upload_planner_media(
            jpeg,
            content_type="image/jpeg",
            file_extension="jpg",
        )
    except MediaHostError:
        raise
    except Exception as exc:
        raise MediaHostError(f"Metricool cover upload failed: {exc}") from exc
    if not isinstance(url, str) or not url.strip():
        raise MediaHostError("Metricool cover upload returned no URL")
    if not is_metricool_static_url(url):
        logger.warning(
            "Cover URL is not on static.metricool.com (UI will not render it): %s",
            url[:160],
        )
    return url.strip()


def cover_png_to_jpeg(settings: Settings, png_path: Path) -> bytes:
    """Convert ``cover-final.png`` to JPEG bytes."""
    jpg_path = png_path.with_name("cover-final.jpg")
    try:
        png_to_jpeg(settings.ffmpeg_bin, png_path, jpg_path)
    except RuntimeError as exc:
        raise MediaHostError(str(exc)) from exc
    data = jpg_path.read_bytes()
    if not data.startswith(_JPEG):
        raise MediaHostError("cover-final.png did not convert to a JPEG")
    return data


def fetch_https_bytes(url: str, *, timeout: float = 120.0) -> bytes:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MediaHostError(f"Could not download cover image: {exc}") from exc
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" in content_type or response.content[:20].lstrip().lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        raise MediaHostError("Cover URL returned HTML instead of an image")
    if not response.content:
        raise MediaHostError("Cover URL returned an empty body")
    return response.content


def resolve_cover_url_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    page_id: str,
    dry_run: bool,
    cover_bytes: bytes | None = None,
    cover_link: str | None = None,
    publication: datetime | None = None,
) -> str | None:
    """Upload the reel cover and return the URL for ``videoThumbnailUrl``.

    ``publication`` is unused: the cover lives on Metricool storage, so it does
    not expire with the post time. A failed upload raises ``MediaHostError``
    and does not fall back to an external link.
    """
    del publication
    size = len(cover_bytes or b"")
    if dry_run:
        logger.info("[dry-run] Would attach cover %s bytes for %s", size, page_id)
        return None

    if cover_bytes:
        work = settings.media_work_dir / page_id.replace("-", "")
        work.mkdir(parents=True, exist_ok=True)
        png_path = work / "cover-final.png"
        png_path.write_bytes(cover_bytes)
        jpeg = cover_png_to_jpeg(settings, png_path)
        return host_cover_jpeg(metricool, jpeg)

    if cover_link and is_metricool_static_url(cover_link):
        logger.info("Reusing Metricool cover URL for %s", page_id)
        return cover_link.strip()

    direct = _fetchable_cover_link(cover_link)
    if direct:
        logger.info("Re-uploading external cover for %s", page_id)
        raw = fetch_https_bytes(direct)
        jpeg = _image_bytes_to_jpeg(settings, raw, work=settings.media_work_dir / "covers")
        return host_cover_jpeg(metricool, jpeg)
    return None


def resolve_miniatura_url(
    raw_url: str | None,
    *,
    metricool: MetricoolClient,
    dry_run: bool,
    settings: Settings | None = None,
) -> str | None:
    """Miniatura must become a static.metricool.com JPEG before it is scheduled.

    Dry-run returns the public URL and does not upload. A URL already on
    ``static.metricool.com`` is reused. Anything else is downloaded and uploaded.
    """
    if not raw_url:
        return None
    url = raw_url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or "x-amz-expires" in {key.lower() for key in parse_qs(parsed.query)}
        or parsed.username
        or parsed.password
        or host in {"localhost", "drive.google.com", "docs.google.com"}
    ):
        logger.warning("Miniatura needs a public HTTPS image, not a preview URL")
        return None
    if host == "dropbox.com" or host.endswith(".dropbox.com"):
        url = dropbox_direct_url(url)
    if dry_run:
        return url
    if is_metricool_static_url(url):
        return url
    if settings is None:
        logger.warning("Miniatura re-upload needs settings; scheduling without thumbnail")
        return None
    try:
        raw = fetch_https_bytes(url)
        jpeg = _image_bytes_to_jpeg(settings, raw, work=settings.media_work_dir / "covers")
        return host_cover_jpeg(metricool, jpeg)
    except MediaHostError as exc:
        logger.warning("Miniatura re-upload failed: %s", exc)
        return None


def _fetchable_cover_link(cover_link: str | None) -> str | None:
    if not cover_link:
        return None
    url = cover_link.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    if host in {"localhost", "drive.google.com", "docs.google.com"}:
        return None
    if "x-amz-expires" in {key.lower() for key in parse_qs(parsed.query)}:
        return None
    if host == "dropbox.com" or host.endswith(".dropbox.com") or "dropboxusercontent.com" in host:
        return dropbox_direct_url(url)
    return url


def _image_bytes_to_jpeg(settings: Settings, data: bytes, *, work: Path) -> bytes:
    if data.startswith(_JPEG):
        return data
    kind = sniff_header(data)
    if kind is None or kind.ext != ".png":
        raise MediaHostError("Cover image is not a JPEG or PNG")
    work.mkdir(parents=True, exist_ok=True)
    png_path = work / "cover-final.png"
    png_path.write_bytes(data)
    return cover_png_to_jpeg(settings, png_path)
