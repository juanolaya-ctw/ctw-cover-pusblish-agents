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
    "pinterest": "pinterest",
    "threads": "threads",
}


def normalize_channel(notion_channel: str | None) -> str | None:
    if not notion_channel:
        return None
    key = notion_channel.strip().lower()
    for token in key.replace("/", " ").split():
        if token in NOTION_TO_METRICOOL:
            return NOTION_TO_METRICOOL[token]
    return NOTION_TO_METRICOOL.get(key)
