"""Pure matching helpers between Notion rows and Metricool scheduled posts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from metricool_sync_posts.metricool.channels import normalize_channel
from metricool_sync_posts.timeutil import dates_equal_within_minutes, notion_date_to_datetime


def normalize_text(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def post_publication_datetime(post: dict[str, Any], tz_name: str) -> datetime | None:
    pd = post.get("publicationDate") or post.get("publication_date")
    if not isinstance(pd, dict):
        return None
    dt_str = pd.get("dateTime") or pd.get("datetime")
    zone = pd.get("timezone") or tz_name
    if not dt_str:
        return None
    naive = datetime.fromisoformat(dt_str)
    return notion_date_to_datetime(naive, zone)


def post_networks(post: dict[str, Any]) -> set[str]:
    nets: set[str] = set()
    for p in post.get("providers") or []:
        if isinstance(p, dict) and p.get("network"):
            nets.add(str(p["network"]).lower())
    for key in post:
        if key.endswith("Data") and isinstance(post[key], dict):
            # e.g. instagramData implies instagram
            base = key[: -len("Data")].lower()
            if base:
                nets.add(base)
    return nets


def post_state(post: dict[str, Any]) -> str:
    """Return PUBLISHED, ERROR, PENDING, SCHEDULED, or UNKNOWN."""
    for field in ("state", "status", "publicationStatus", "postStatus"):
        val = post.get(field)
        if isinstance(val, str):
            u = val.upper()
            if "ERROR" in u or "FAIL" in u:
                return "ERROR"
            if "PUBLISH" in u and "UN" not in u:
                return "PUBLISHED"
            if "PEND" in u:
                return "PENDING"
            if "SCHED" in u:
                return "SCHEDULED"
    published = post.get("published") or post.get("isPublished")
    if published is True:
        return "PUBLISHED"
    if published is False:
        return "SCHEDULED"
    return "UNKNOWN"


def match_score(
    *,
    notion_caption: str,
    notion_channel: str | None,
    notion_publication: datetime,
    metricool_post: dict[str, Any],
    tz_name: str,
) -> float:
    """Higher is better; 0 means no match."""
    mc_date = post_publication_datetime(metricool_post, tz_name)
    if mc_date is None:
        return 0.0
    if not dates_equal_within_minutes(notion_publication, mc_date, minutes=5):
        return 0.0
    net = normalize_channel(notion_channel)
    if net:
        nets = post_networks(metricool_post)
        if nets and net not in nets:
            return 0.0
    mc_text = normalize_text(str(metricool_post.get("text") or ""))
    no_text = normalize_text(notion_caption)
    if mc_text and no_text:
        if mc_text == no_text:
            return 10.0
        if mc_text in no_text or no_text in mc_text:
            return 7.0
        # prefix match
        if mc_text[:40] == no_text[:40]:
            return 5.0
        return 0.0
    return 3.0


def find_best_match(
    notion_caption: str,
    notion_channel: str | None,
    notion_publication: datetime,
    candidates: list[dict[str, Any]],
    tz_name: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for post in candidates:
        score = match_score(
            notion_caption=notion_caption,
            notion_channel=notion_channel,
            notion_publication=notion_publication,
            metricool_post=post,
            tz_name=tz_name,
        )
        if score > best_score:
            best_score = score
            best = post
    return best if best_score > 0 else None
