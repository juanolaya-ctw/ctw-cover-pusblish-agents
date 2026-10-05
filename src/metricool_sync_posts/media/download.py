from __future__ import annotations

import logging
from pathlib import Path

import httpx

from metricool_sync_posts.media.urls import (
    dropbox_direct_url,
    google_drive_direct_url,
    is_dropbox_url,
    is_google_drive_url,
)

logger = logging.getLogger(__name__)


def resolve_download_url(url: str) -> str:
    if is_dropbox_url(url):
        return dropbox_direct_url(url)
    if is_google_drive_url(url):
        return google_drive_direct_url(url)
    return url


def download_to_path(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    direct = resolve_download_url(url)
    with httpx.stream("GET", direct, follow_redirects=True, timeout=300.0) as resp:
        resp.raise_for_status()
        with dest.open("wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    logger.info("Downloaded media to %s", dest)
    return dest
