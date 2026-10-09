"""Build Metricool post bodies from Notion content types (Flow 1)."""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from typing import Any

from metricool_sync_posts.media.pipeline import IMAGE_EXT, VIDEO_EXT, extension_from_url
from metricool_sync_posts.metricool.channels import label_is_youtube_short, normalize_channel
from metricool_sync_posts.timeutil import iso_metricool

# ScheduledPostTikTokData in the Metricool swagger has photoCoverIndex and
# autoAddMusic. That is a TikTok photo post (one image or a carousel). YouTube's
# ScheduledPostYoutubeData only has video types (short/video), so an image-only
# post drops YouTube and keeps Instagram and TikTok. The row is skipped only
# when no network remains.
TIKTOK_PHOTO_POSTS = True

logger = logging.getLogger(__name__)


def fold_text(value: str | None) -> str:
    """Lowercase and strip accents so 'estática' and 'estatica' compare equal."""
    raw = (value or "").strip().lower()
    decomposed = unicodedata.normalize("NFD", raw)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def is_trials_reel(title: str) -> bool:
    return "trials" in fold_text(title)


def is_miniatura_type(content_type: str | None) -> bool:
    """Thumbnail rows (Miniatura/Miniaturas) are not standalone posts."""
    return "miniatura" in fold_text(content_type)


def is_story_type(content_type: str | None) -> bool:
    """Historias / stories. Checked before a static piece on the same row."""
    ct = fold_text(content_type)
    return "historia" in ct or "stories" in ct or "story" in ct


def _media_kind(url: str) -> str:
    ext = extension_from_url(url)
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def urls_are_only_images(urls: list[str] | None) -> bool:
    """True when every URL is an image and there is at least one."""
    if not urls:
        return False
    return all(_media_kind(url) == "image" for url in urls)


def partition_image_networks(
    networks: list[str],
    media_urls: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Drop networks that cannot accept an image-only payload.

    YouTube's swagger type is only short/video, so it is removed. Instagram
    stays. TikTok stays as a photo post (photoCoverIndex) when the swagger
    allows it. Order of the remaining networks is preserved.
    """
    ordered: list[str] = []
    for net in networks:
        key = str(net).strip().lower()
        if key and key not in ordered:
            ordered.append(key)
    if not urls_are_only_images(media_urls):
        return ordered, []
    dropped = [net for net in ordered if net == "youtube" or (
        net == "tiktok" and not TIKTOK_PHOTO_POSTS
    )]
    kept = [net for net in ordered if net not in dropped]
    return kept, dropped


def video_network_skip_reason(
    networks: list[str],
    media_urls: list[str] | None,
) -> str | None:
    """Whole-row skip only when image media leaves no network to post."""
    kept, dropped = partition_image_networks(networks, media_urls)
    if dropped and not kept:
        return "no_video_for_network"
    return None


def infer_instagram_type(title: str, content_type: str | None) -> str:
    ct = fold_text(content_type)
    if is_story_type(content_type):
        return "STORY"
    if is_trials_reel(title):
        return "TRIAL_REEL"
    # Metricool rejects instagramData.type CAROUSEL. Several media URLs on POST
    # are how the API represents a carousel. Valid types: POST, REEL, TRIAL_REEL, STORY.
    if "carrusel" in ct or "carousel" in ct:
        return "POST"
    # 'Piezas estática' / estático / estatica — before the reel/video default.
    if any(token in ct for token in ("estatic", "static", "imagen", "foto")):
        return "POST"
    if "reel" in ct or "video" in ct:
        return "REEL"
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
    networks: list[str] | None = None,
    youtube_short: bool = False,
    youtube_title: str | None = None,
) -> dict[str, Any]:
    if is_miniatura_type(content_type):
        raise ValueError(f"Miniatura rows are not scheduled as posts: {content_type!r}")
    chosen: list[str] = []
    if networks:
        for net in networks:
            key = str(net).strip().lower()
            if not key or key == "linkedin" or key in chosen:
                continue
            chosen.append(key)
        if not chosen:
            raise ValueError("LinkedIn is not scheduled by this pipeline")
    else:
        network = normalize_channel(channel, title=title)
        if not network:
            raise ValueError(f"Unknown or ambiguous channel: {channel!r}")
        if network == "linkedin":
            raise ValueError("LinkedIn is not scheduled by this pipeline")
        chosen = [network]
    if channel and label_is_youtube_short(channel):
        youtube_short = True
    body: dict[str, Any] = {
        "publicationDate": {
            "dateTime": iso_metricool(publication),
            "timezone": tz_name,
        },
        "text": caption,
        "providers": [{"network": net} for net in chosen],
    }
    urls = list(media_urls or [])
    if not urls and media_url:
        urls = [media_url]

    if media_id:
        body["media"] = {"mediaId": media_id}
    elif urls:
        body["media"] = urls

    folded_type = fold_text(content_type)
    if "youtube" in chosen:
        if youtube_short or "short" in folded_type or "clip" in folded_type:
            yt_type = "short"
        elif youtube_existing_video:
            yt_type = "video"
        else:
            yt_type = "video"
        # Public YouTube title is the Titulo hook, never the task title or caption.
        body["youtubeData"] = {
            "title": (youtube_title or "").strip()[:100],
            "type": yt_type,
            "privacy": "public",
            "madeForKids": False,
            "isAiGeneratedContent": False,
        }
    if "instagram" in chosen:
        ig_type = infer_instagram_type(title, content_type)
        ig_data: dict[str, Any] = {"type": ig_type, "autoPublish": True}
        if cover_url and ig_type in ("REEL", "TRIAL_REEL"):
            # Official ScheduledPost schema: thumbnail is top-level, never extra media.
            body["videoThumbnailUrl"] = cover_url
        body["instagramData"] = ig_data
    if "tiktok" in chosen and urls_are_only_images(urls):
        # Swagger ScheduledPostTikTokData.photoCoverIndex: 0 is the first image.
        body["tiktokData"] = {"photoCoverIndex": 0}
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
