"""Map Notion Canal values to Metricool provider network names."""

from __future__ import annotations

NOTION_TO_METRICOOL: dict[str, str] = {
    "instagram": "instagram",
    "ig": "instagram",
    "facebook": "facebook",
    "fb": "facebook",
    "linkedin": "linkedin",
    "twitter": "twitter",
    "x": "twitter",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "yt": "youtube",
    "pinterest": "pinterest",
    "threads": "threads",
}


def normalize_channel(notion_channel: str | None, *, title: str | None = None) -> str | None:
    """Map a Canal label to one Metricool network.

    LinkedIn is removed when another network is present (multi-network rows
    keep the non-LinkedIn destination). A LinkedIn-only label still returns
    ``linkedin`` so callers can exclude it. ``title`` is accepted for
    caller compatibility and does not change the network.
    """
    _ = title
    if not notion_channel:
        return None
    key = notion_channel.strip().lower()
    tokens = key.replace("/", " ").replace("+", " ").replace(",", " ").split()
    networks = {
        NOTION_TO_METRICOOL[token]
        for token in tokens
        if token in NOTION_TO_METRICOOL
    }
    if "linkedin" in networks and len(networks) > 1:
        networks.discard("linkedin")
    if len(networks) == 1:
        return next(iter(networks))
    return None
