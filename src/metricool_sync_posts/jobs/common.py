from __future__ import annotations

import re
from datetime import datetime, timedelta

from metricool_sync_posts.config import Settings
from metricool_sync_posts.notion.properties import NotionPostRow
from metricool_sync_posts.timeutil import notion_date_to_datetime

# Bracketed blanks and curly placeholders. TODO/TBD/XXX must be the uppercase
# token: Spanish "todo" is a normal word, not a draft marker.
_BRACKET_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}")
_LOREM_PLACEHOLDER = re.compile(r"lorem ipsum", re.IGNORECASE)
_UPPER_MARKER = re.compile(r"\b(?:XXX+|TODO|TBD)\b")


def caption_has_placeholder(caption: str) -> bool:
    """True when the caption still has an obvious unfilled placeholder."""
    if not caption:
        return False
    if _BRACKET_PLACEHOLDER.search(caption) or _LOREM_PLACEHOLDER.search(caption):
        return True
    return any(match.group(0).isupper() for match in _UPPER_MARKER.finditer(caption))


def caption_for_row(notion, row: NotionPostRow) -> str:
    if row.caption.strip():
        return row.caption.strip()
    return notion.page_body_plain_text(row.page_id)


def publication_dt(row: NotionPostRow, tz_name: str) -> datetime | None:
    if row.publication is None:
        return None
    return notion_date_to_datetime(row.publication, tz_name)


def metricool_fetch_window_for_confirm(settings: Settings) -> tuple[datetime, datetime]:
    """7 days back → +1 day (Bogotá), per spec."""
    from metricool_sync_posts.timeutil import now_in

    now = now_in(settings.timezone)
    start = now - timedelta(days=7)
    end = now + timedelta(days=1)
    return start, end


def metricool_fetch_window_for_sync(settings: Settings) -> tuple[datetime, datetime]:
    from metricool_sync_posts.timeutil import now_in

    now = now_in(settings.timezone)
    days = settings.publication_window_days
    start = now - timedelta(days=days)
    end = now + timedelta(days=days)
    return start, end
