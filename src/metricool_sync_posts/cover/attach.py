"""Resolve optional Instagram thumbnails without Dropbox OAuth."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.upload_public import upload_public_url
from metricool_sync_posts.media.urls import dropbox_direct_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)


def resolve_cover_url_for_metricool(
    *,
    settings: Settings,
    metricool: MetricoolClient,
    cover_bytes: bytes,
    page_id: str,
    dry_run: bool,
) -> str | None:
    """
    Turn cover PNG/JPEG bytes into a URL Metricool can consume.

    Metricool ScheduledPost accepts videoThumbnailUrl at the top level.
    """
    size = len(cover_bytes)
    if dry_run:
        logger.info("[dry-run] Would attach cover %s bytes for %s", size, page_id)
        return None

    work = settings.media_work_dir
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"cover-{page_id.replace('-', '')}.png"
    path.write_bytes(cover_bytes)

    try:
        public_url = upload_public_url(settings, path)
    except Exception as exc:
        logger.warning(
            "Cover ready (%s bytes) for %s but public upload failed (%s); "
            "scheduling without cover URL",
            size,
            page_id,
            exc,
        )
        return None
    norm = metricool.normalize_media_url(public_url)
    return norm.get("url") or norm.get("normalizedUrl") or public_url


def resolve_miniatura_url(
    raw_url: str | None, *, metricool: MetricoolClient, dry_run: bool
) -> str | None:
    """Miniatura must be a public image URL, not a Drive preview or folder.

    Public Drive /uc?export=download&id=FILE links are allowed, but a
    successful normalize/pilot is still needed to prove public image access.

    Normalization is skipped in dry-run. Temporary Notion file URLs need a
    durable external image URL for scheduling beyond their expiry.
    """
    if not raw_url:
        return None
    url = raw_url.strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query, keep_blank_values=True)
    direct_drive = (
        host == "drive.google.com"
        and parsed.netloc == "drive.google.com"
        and parsed.path == "/uc"
        and not parsed.fragment
        and set(query) == {"export", "id"}
        and query.get("export") == ["download"]
        and len(query.get("id", [])) == 1
        and re.fullmatch(r"[A-Za-z0-9_-]+", query["id"][0]) is not None
    )
    if (
        parsed.scheme != "https"
        or not host
        or "x-amz-expires" in {k.lower() for k in parse_qs(parsed.query)}
        or parsed.username
        or parsed.password
        or host in {"localhost", "docs.google.com"}
        or (host == "drive.google.com" and not direct_drive)
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
