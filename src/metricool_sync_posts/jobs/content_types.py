"""Build Metricool post bodies from Notion content types (Flow 1)."""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime
from typing import Any

from metricool_sync_posts.media.pipeline import (
    IMAGE_EXT,
    VIDEO_EXT,
    extension_from_url,
    strip_extension_hint,
)
from metricool_sync_posts.metricool.channels import label_is_youtube_short, normalize_channel
from metricool_sync_posts.metricool.matching import (
    FORMAT_INSTAGRAM_FEED,
    FORMAT_INSTAGRAM_STORY,
    FORMAT_TIKTOK,
    FORMAT_YOUTUBE,
)
from metricool_sync_posts.timeutil import iso_metricool

# ScheduledPostTikTokData in the Metricool swagger has photoCoverIndex and
# autoAddMusic. That is a TikTok photo post (one image or a carousel). YouTube's
# ScheduledPostYoutubeData only has video types (short/video), so an image-only
# post drops YouTube and keeps Instagram and TikTok. The row is skipped only
# when no network remains.
TIKTOK_PHOTO_POSTS = True
# Omitting these leaves TikTok in manual-notification mode: Metricool later
# shows Published with no TikTok post id or link. privacyOption and the three
# interaction flags are the ScheduledPostTikTokData defaults the API expects.
TIKTOK_PRIVACY_PUBLIC = "PUBLIC_TO_EVERYONE"

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


def tiktok_schedule_data(*, photo: bool) -> dict[str, Any]:
    """TikTok block for every TikTok post, video or image.

    ``autoPublish`` itself is top-level on the ScheduledPost (same flag as the
    planner's Auto publish button). ``privacyOption`` has to be explicit or
    TikTok rejects the post and Metricool falls back to a manual notification.
    """
    data: dict[str, Any] = {
        "privacyOption": TIKTOK_PRIVACY_PUBLIC,
        "disableComment": False,
        "disableDuet": False,
        "disableStitch": False,
    }
    if photo:
        # Swagger ScheduledPostTikTokData.photoCoverIndex: 0 is the first image.
        data["photoCoverIndex"] = 0
    return data


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


def publication_format_groups(
    networks: list[str],
    *,
    title: str | None,
    content_type: str | None,
) -> frozenset[str]:
    """Format groups this row will publish on.

    Instagram Stories only share a slot with Stories. POST (static and
    carousel), REEL, and TRIAL_REEL share Instagram feed. TikTok only shares
    with TikTok, and YouTube only with YouTube.
    """
    groups: set[str] = set()
    for net in networks:
        key = str(net).strip().lower()
        if key == "instagram":
            ig_type = infer_instagram_type(title or "", content_type)
            if ig_type == "STORY":
                groups.add(FORMAT_INSTAGRAM_STORY)
            else:
                groups.add(FORMAT_INSTAGRAM_FEED)
        elif key == "tiktok":
            groups.add(FORMAT_TIKTOK)
        elif key == "youtube":
            groups.add(FORMAT_YOUTUBE)
    return frozenset(groups)


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
    image_only = urls_are_only_images(urls)
    # Fragments are a local type hint for extension-less Drive URLs. Metricool
    # stores the URL it is given, so the hint must not be part of that URL.
    urls = [strip_extension_hint(url) for url in urls]
    if cover_url:
        cover_url = strip_extension_hint(cover_url)

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
            # Official ScheduledPost schema: the cover is top-level videoThumbnailUrl,
            # never a field on instagramData. That URL is shared by every provider.
            # schedule_bodies() keeps it on Instagram and posts YouTube separately.
            body["videoThumbnailUrl"] = cover_url
        body["instagramData"] = ig_data
    if "tiktok" in chosen:
        # Top-level autoPublish is the planner switch. Without it TikTok stays
        # in manual notification mode even when the provider later says Published.
        body["autoPublish"] = True
        body["tiktokData"] = tiktok_schedule_data(photo=image_only)
    return body


# Top-level ScheduledPost fields that would upload or select a cover for every
# provider. ScheduledPostInstagramData and ScheduledPostYoutubeData have no
# thumbnail property, and media is a list of URL strings (swagger, 2026-10).
_SHARED_COVER_KEYS = frozenset(
    {
        "videoThumbnailUrl",
        "videoThumbnail",
        "thumbnail",
        "thumbnailUrl",
        "coverUrl",
        "videoCoverUrl",
        "videoCoverMilliseconds",
    }
)


def _provider_networks(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for provider in body.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("network"):
            continue
        name = str(provider["network"]).strip().lower()
        if name and name not in names:
            names.append(name)
    return names


def _copy_post_body(body: dict[str, Any]) -> dict[str, Any]:
    copied = dict(body)
    providers = copied.get("providers")
    if isinstance(providers, list):
        copied["providers"] = [
            dict(item) if isinstance(item, dict) else item for item in providers
        ]
    media = copied.get("media")
    if isinstance(media, list):
        copied["media"] = list(media)
    for key, value in list(copied.items()):
        if isinstance(value, dict):
            copied[key] = dict(value)
    return copied


def _value_set(value: Any) -> bool:
    return value not in (None, "", [], {})


def _has_shared_cover(body: dict[str, Any]) -> bool:
    """True when a top-level cover would be sent with every provider."""
    for key, value in body.items():
        if str(key).endswith("Data") or not _value_set(value):
            continue
        folded = str(key).lower()
        if key in _SHARED_COVER_KEYS or "thumbnail" in folded or folded.endswith("coverurl"):
            return True
    return False


def _without_youtube_cover(body: dict[str, Any]) -> dict[str, Any]:
    """YouTube post: the video only, so YouTube generates the frame."""
    cleaned = _copy_post_body(body)
    for key in list(cleaned):
        folded = str(key).lower()
        if key in _SHARED_COVER_KEYS or "thumbnail" in folded or folded.endswith("coverurl"):
            cleaned.pop(key, None)
    cleaned["providers"] = [{"network": "youtube"}]
    for key in list(cleaned):
        if str(key).endswith("Data") and key != "youtubeData":
            cleaned.pop(key, None)
    youtube = cleaned.get("youtubeData")
    if isinstance(youtube, dict):
        cleaned["youtubeData"] = {
            key: value
            for key, value in youtube.items()
            if "thumb" not in key.lower() and "cover" not in key.lower()
        }
    return cleaned


def _strip_youtube_data_cover(body: dict[str, Any]) -> dict[str, Any]:
    youtube = body.get("youtubeData")
    if not isinstance(youtube, dict):
        return body
    kept = {
        key: value
        for key, value in youtube.items()
        if "thumb" not in key.lower() and "cover" not in key.lower()
    }
    if kept == youtube:
        return body
    copied = _copy_post_body(body)
    copied["youtubeData"] = kept
    return copied


def schedule_bodies(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Posts to create for one Notion row.

    ``videoThumbnailUrl`` is one string on the whole ScheduledPost. Instagram
    reel covers use it (4:5). YouTube has no separate thumbnail field, so a
    combined post would upload that same image as the YouTube thumbnail.
    When the row has both, YouTube is its own post with every cover field
    omitted and the other networks keep the reel cover.
    """
    networks = _provider_networks(body)
    if "youtube" not in networks:
        return [body]
    stripped = _strip_youtube_data_cover(body)
    if not _has_shared_cover(stripped):
        return [stripped]
    others = [network for network in networks if network != "youtube"]
    if not others:
        return [_without_youtube_cover(stripped)]
    kept = _copy_post_body(stripped)
    kept["providers"] = [{"network": network} for network in others]
    kept.pop("youtubeData", None)
    return [kept, _without_youtube_cover(stripped)]


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
