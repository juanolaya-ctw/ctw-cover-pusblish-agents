"""Resolve optional Instagram thumbnails without Dropbox OAuth."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.upload_public import upload_public_url
from metricool_sync_posts.media.urls import dropbox_direct_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)


def _direct_cover_link(cover_link: str | None) -> str | None:
    """Dropbox (rewritten to dl=1) or another public https image. Not Drive or previews."""
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


def _normalized_url(metricool: MetricoolClient, public_url: str) -> str:
    try:
        norm = metricool.normalize_media_url(public_url)
    except Exception:
        logger.warning("Cover URL normalization failed; using the public URL as-is")
        return public_url
    if isinstance(norm, dict):
        return norm.get("url") or norm.get("normalizedUrl") or public_url
    return public_url


def resolve_cover_url_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    page_id: str,
    dry_run: bool,
    cover_bytes: bytes | None = None,
    cover_link: str | None = None,
) -> str | None:
    """
    Turn cover PNG bytes into a URL Metricool can consume.

    Prefers a host that lasts at least 72 hours (S3, then litterbox 72h).
    A Dropbox ``cover_link`` is the next choice. uguu.se is only a last resort.
    Metricool ScheduledPost accepts videoThumbnailUrl at the top level.
    """
    size = len(cover_bytes or b"")
    if dry_run:
        logger.info("[dry-run] Would attach cover %s bytes for %s", size, page_id)
        return None

    path = None
    if cover_bytes:
        work = settings.media_work_dir
        work.mkdir(parents=True, exist_ok=True)
        path = work / f"cover-{page_id.replace('-', '')}.png"
        path.write_bytes(cover_bytes)
        try:
            public_url = upload_public_url(settings, path, min_hours=72, allow_short=False)
            return _normalized_url(metricool, public_url)
        except Exception as exc:
            logger.warning(
                "Durable cover upload failed for %s (%s bytes): %s",
                page_id,
                size,
                exc,
            )

    direct = _direct_cover_link(cover_link)
    if direct:
        logger.info("Using direct cover URL for %s", page_id)
        return _normalized_url(metricool, direct)

    if path is not None:
        try:
            public_url = upload_public_url(settings, path, min_hours=72, allow_short=True)
            return _normalized_url(metricool, public_url)
        except Exception as exc:
            logger.warning(
                "Cover ready (%s bytes) for %s but public upload failed (%s)",
                size,
                page_id,
                exc,
            )
    return None


def resolve_miniatura_url(
    raw_url: str | None, *, metricool: MetricoolClient, dry_run: bool
) -> str | None:
    """Miniatura must be a public image URL, not a Drive preview or folder.

    Normalization is skipped in dry-run. Temporary Notion file URLs need a
    durable external image URL for scheduling beyond their expiry.
    """
    if not raw_url:
        return None
    url = raw_url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or "x-amz-expires" in {k.lower() for k in parse_qs(parsed.query)}
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
    try:
        normalized = metricool.normalize_media_url(url)
        return normalized.get("url") or normalized.get("normalizedUrl") or url
    except Exception:
        logger.warning("Miniatura normalization failed; scheduling without thumbnail")
        return None
