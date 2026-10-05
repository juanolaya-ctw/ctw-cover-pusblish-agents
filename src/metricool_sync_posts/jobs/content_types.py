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
    if is_trials_reel(title):
        return "TRIAL_REEL"
    ct = (content_type or "").lower()
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
    media_url: str | None,
    media_id: str | None = None,
    cover_url: str | None = None,
) -> dict[str, Any]:
    network = normalize_channel(channel) or "instagram"
    body: dict[str, Any] = {
        "publicationDate": {
            "dateTime": iso_metricool(publication),
            "timezone": tz_name,
        },
        "text": caption,
        "providers": [{"network": network}],
    }
    if media_id:
        body["media"] = {"mediaId": media_id}
    elif media_url:
        body["media"] = [{"url": media_url}]

    if network == "instagram":
        ig_type = infer_instagram_type(title, content_type)
        body["instagramData"] = {"type": ig_type, "autoPublish": True}
        # TODO(pilot): Metricool planner JSON for reel/trial cover thumbnail — set field
        # once confirmed via DevTools (e.g. coverUrl / thumbnail on instagramData).
        if cover_url:
            logger.info(
                "Cover URL ready for %s (%s); not attached to payload until pilot field known",
                title or "post",
                cover_url[:80],
            )
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
