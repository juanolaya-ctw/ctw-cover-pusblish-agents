"""Connected networks for one Metricool brand (GET /admin/simpleProfiles)."""

from __future__ import annotations

import logging
from typing import Any

from metricool_sync_posts.metricool.channels import (
    FALLBACK_CONNECTED_NETWORKS,
    NEVER_SCHEDULE_NETWORKS,
)

logger = logging.getLogger(__name__)

# simpleProfiles field → Metricool provider network.
# fbBusinessId is the Instagram business id, not a Facebook page, so it is absent.
_PROFILE_NETWORK_FIELDS = {
    "instagram": "instagram",
    "facebook": "facebook",
    "twitter": "twitter",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "pinterest": "pinterest",
    "threads": "threads",
    "twitch": "twitch",
    "bluesky": "bluesky",
    "linkedincompany": "linkedin",
    "linkedin": "linkedin",
}


def _connected_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and text.casefold() not in {"0", "null", "none"}
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, dict):
        return any(_connected_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_connected_value(item) for item in value)
    return False


def profile_blog_id(profile: dict[str, Any]) -> str:
    for key in ("id", "blogId", "blog_id"):
        if profile.get(key) is not None:
            return str(profile[key]).strip()
    return ""


def connected_networks_from_profile(profile: dict[str, Any]) -> frozenset[str]:
    """Networks with a real account on this brand. ``fbBusinessId`` is not Facebook."""
    found: set[str] = set()
    for key, value in profile.items():
        network = _PROFILE_NETWORK_FIELDS.get(str(key).casefold())
        if network and _connected_value(value):
            found.add(network)
    return frozenset(found)


def select_brand_profile(profiles: list[dict[str, Any]], blog_id: str) -> dict[str, Any] | None:
    want = str(blog_id).strip()
    for profile in profiles:
        if isinstance(profile, dict) and profile_blog_id(profile) == want:
            return profile
    return None


def publishable_networks(connected: frozenset[str]) -> frozenset[str]:
    """Connected networks this pipeline is allowed to post to."""
    return frozenset(net for net in connected if net not in NEVER_SCHEDULE_NETWORKS)


def load_connected_networks(metricool, blog_id: str) -> frozenset[str]:
    """Networks to schedule for this run.

    Reads ``GET /admin/simpleProfiles`` and keeps the profile for
    ``blog_id``. LinkedIn is removed even when ``linkedinCompany`` is set.
    Any failure uses Instagram, TikTok, and YouTube.
    """
    blog_id = str(blog_id).strip()
    try:
        raw = metricool.get_simple_profiles()
    except Exception:
        logger.warning(
            "simpleProfiles failed for blog %s; using %s",
            blog_id,
            sorted(FALLBACK_CONNECTED_NETWORKS),
            exc_info=True,
        )
        return FALLBACK_CONNECTED_NETWORKS

    profiles: list[dict[str, Any]] | None
    if isinstance(raw, list):
        profiles = [item for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        profiles = None
        for key in ("data", "profiles", "items", "results"):
            inner = raw.get(key)
            if isinstance(inner, list):
                profiles = [item for item in inner if isinstance(item, dict)]
                break
    else:
        profiles = None

    if not profiles:
        logger.warning(
            "simpleProfiles returned no profiles for blog %s; using %s",
            blog_id,
            sorted(FALLBACK_CONNECTED_NETWORKS),
        )
        return FALLBACK_CONNECTED_NETWORKS

    profile = select_brand_profile(profiles, blog_id)
    if profile is None:
        logger.warning(
            "Blog %s was not in simpleProfiles; using %s",
            blog_id,
            sorted(FALLBACK_CONNECTED_NETWORKS),
        )
        return FALLBACK_CONNECTED_NETWORKS

    connected = connected_networks_from_profile(profile)
    publishable = publishable_networks(connected)
    if not publishable:
        logger.warning(
            "Blog %s has no publishable network in simpleProfiles; using %s",
            blog_id,
            sorted(FALLBACK_CONNECTED_NETWORKS),
        )
        return FALLBACK_CONNECTED_NETWORKS
    logger.info(
        "Metricool blog %s connected networks: %s; scheduling %s",
        blog_id,
        sorted(connected),
        sorted(publishable),
    )
    return publishable
