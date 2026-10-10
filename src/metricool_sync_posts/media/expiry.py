"""Pre-publish check: Metricool must still be able to fetch each media URL."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

_LOOKAHEAD = timedelta(hours=24)


def due_within_24h(publication: datetime | None, now: datetime) -> bool:
    if publication is None:
        return False
    if publication.tzinfo is None and now.tzinfo is not None:
        publication = publication.replace(tzinfo=now.tzinfo)
    if now.tzinfo is None and publication.tzinfo is not None:
        now = now.replace(tzinfo=publication.tzinfo)
    return now <= publication <= now + _LOOKAHEAD


def post_media_urls(post: dict) -> list[str]:
    """Media and cover URLs stored on a Metricool scheduled post."""
    found: list[str] = []
    media = post.get("media")
    if isinstance(media, str) and media.strip():
        found.append(media.strip())
    elif isinstance(media, list):
        for item in media:
            if isinstance(item, str) and item.strip():
                found.append(item.strip())
            elif isinstance(item, dict):
                for key in ("url", "mediaUrl", "src"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        found.append(value.strip())
                        break
    for key in ("videoThumbnailUrl", "videoThumbnail"):
        value = post.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def media_url_live(url: str, *, client: httpx.Client | None = None) -> tuple[bool, int]:
    """HEAD, then GET. Live only when one of them returns 200."""
    own = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        head_status = _status(http, "HEAD", url)
        if head_status == 200:
            return True, 200
        get_status = _status(http, "GET", url)
        return get_status == 200, get_status or head_status
    finally:
        if own:
            http.close()


def _status(http: httpx.Client, method: str, url: str) -> int:
    try:
        if method == "GET":
            with http.stream("GET", url, follow_redirects=True) as resp:
                return resp.status_code
        resp = http.request(method, url, follow_redirects=True)
        return resp.status_code
    except httpx.HTTPError as exc:
        logger.warning("%s %s failed: %s", method, url[:120], exc)
        return 0
