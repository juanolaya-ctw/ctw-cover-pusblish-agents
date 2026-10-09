"""Build Metricool post bodies from Notion content types (Flow 1)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from metricool_sync_posts.metricool.channels import normalize_channel
from metricool_sync_posts.timeutil import iso_metricool

logger = logging.getLogger(__name__)


def is_trials_reel(title: str) -> bool:
    return "trials" in title.lower()


def infer_instagram_type(title: str, content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "historia" in ct or "stories" in ct or "story" in ct:
        return "STORIES"
    if is_trials_reel(title):
        return "TRIAL_REEL"
    if "carrusel" in ct or "carousel" in ct:
        return "CAROUSEL"
    if "reel" in ct or "video" in ct:
        return "REEL"
    if "imagen" in ct or "static" in ct or "foto" in ct:
        return "POST"
    return "REEL"


def build_schedule_body(
    *,
    caption: str,
    publication: datetime,
    tz_name: str,
    channel: str | None,
    title: str,
    content_type: str | None,
    media_url: str | None = None,
    media_urls: list[str] | None = None,
    media_id: str | None = None,
    cover_url: str | None = None,
    youtube_existing_video: bool = False,
) -> dict[str, Any]:
    network = normalize_channel(channel, title=title)
    if not network:
        raise ValueError(f"Unknown or ambiguous channel: {channel!r}")
    body: dict[str, Any] = {
        "publicationDate": {
            "dateTime": iso_metricool(publication),
            "timezone": tz_name,
        },
        "text": caption,
        "providers": [{"network": network}],
    }
    urls = list(media_urls or [])
    if not urls and media_url:
        urls = [media_url]

    if media_id:
        body["media"] = {"mediaId": media_id}
    elif urls:
        body["media"] = urls

    if network == "youtube":
        yt_type = "short" if content_type and "short" in content_type.lower() else "video"
        if content_type and "clip" in content_type.lower():
            yt_type = "short"
        if youtube_existing_video:
            yt_type = "video"
        body["youtubeData"] = {
            "title": (title or caption)[:100],
            "type": yt_type,
            "privacy": "public",
            "madeForKids": False,
            "isAiGeneratedContent": False,
        }
    elif network == "instagram":
        ig_type = infer_instagram_type(title, content_type)
        ig_data: dict[str, Any] = {"type": ig_type, "autoPublish": True}
        if cover_url and ig_type in ("REEL", "TRIAL_REEL"):
            # Official ScheduledPost schema: thumbnail is top-level, never extra media.
            body["videoThumbnailUrl"] = cover_url
        body["instagramData"] = ig_data
    return body


def merge_publication_date(
    existing_post: dict[str, Any],
    new_publication: datetime,
    tz_name: str,
) -> dict[str, Any]:
    """Return full post payload for PUT update with new publicationDate."""
    updated = dict(existing_post)
    updated["publicationDate"] = {
        "dateTime": iso_metricool(new_publication),
        "timezone": tz_name,
    }
    return updated
