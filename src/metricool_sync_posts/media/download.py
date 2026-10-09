from __future__ import annotations

import logging
from pathlib import Path

import httpx

from metricool_sync_posts.media.urls import (
    dropbox_download_candidates,
    google_drive_direct_url,
    is_dropbox_url,
    is_google_drive_url,
)

logger = logging.getLogger(__name__)


def resolve_download_url(url: str) -> str:
    if is_dropbox_url(url):
        return dropbox_download_candidates(url)[0]
    if is_google_drive_url(url):
        return google_drive_direct_url(url)
    return url


def _looks_like_html(content_type: str | None, first_chunk: bytes) -> bool:
    ct = (content_type or "").lower()
    if "text/html" in ct:
        return True
    head = first_chunk[:200].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def download_to_path(url: str, dest: Path) -> Path:
    """
    Download media to dest.

    Dropbox public/share links: try dl=1 / raw=1 / dl.dropboxusercontent.com
    without any DROPBOX_ACCESS_TOKEN.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_dropbox_url(url):
        candidates = dropbox_download_candidates(url)
    elif is_google_drive_url(url):
        candidates = [google_drive_direct_url(url)]
    else:
        candidates = [url]

    last_error: Exception | None = None
    for idx, direct in enumerate(candidates):
        try:
            with httpx.stream("GET", direct, follow_redirects=True, timeout=300.0) as resp:
                resp.raise_for_status()
                # Buffer first chunk to reject HTML interstitial / login pages
                iterator = resp.iter_bytes()
                try:
                    first = next(iterator)
                except StopIteration:
                    first = b""
                if _looks_like_html(resp.headers.get("content-type"), first):
                    raise RuntimeError(
                        f"Dropbox/Drive returned HTML instead of a file for {direct[:120]}"
                    )
                with dest.open("wb") as f:
                    f.write(first)
                    for chunk in iterator:
                        f.write(chunk)
            logger.info("Downloaded media to %s (candidate %s)", dest, idx + 1)
            return dest
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Download candidate %s/%s failed for %s: %s",
                idx + 1,
                len(candidates),
                direct[:100],
                exc,
            )
            if dest.exists():
                dest.unlink(missing_ok=True)

    assert last_error is not None
    raise last_error
