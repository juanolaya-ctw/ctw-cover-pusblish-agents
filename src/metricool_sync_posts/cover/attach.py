"""Upload cover bytes to a public URL for Metricool (field name TBD in API)."""

from __future__ import annotations

import logging

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.upload_public import upload_public_url
from metricool_sync_posts.metricool.client import MetricoolClient

logger = logging.getLogger(__name__)


def _can_upload_public(settings: Settings) -> bool:
    if settings.transfer_sh_enabled:
        return True
    return bool(
        settings.s3_endpoint
        and settings.s3_bucket
        and settings.s3_access_key
        and settings.s3_secret_key
    )


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

    TODO(pilot): confirm JSON field for reel/trial cover in planner DevTools
    (instagramData thumbnail / coverImage — not wired in build_schedule_body yet).
    """
    size = len(cover_bytes)
    if dry_run:
        logger.info("[dry-run] Would attach cover %s bytes for %s", size, page_id)
        return None

    if not _can_upload_public(settings):
        logger.warning(
            "Cover ready (%s bytes) for %s but no public upload "
            "(enable TRANSFER_SH or S3_*); scheduling without cover URL",
            size,
            page_id,
        )
        return None

    work = settings.media_work_dir
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"cover-{page_id.replace('-', '')}.png"
    path.write_bytes(cover_bytes)

    public_url = upload_public_url(settings, path)
    norm = metricool.normalize_media_url(public_url)
    return norm.get("url") or norm.get("normalizedUrl") or public_url
