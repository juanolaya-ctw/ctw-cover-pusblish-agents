"""Fail-closed schedule attempts. Requires persistent storage across live runs."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from metricool_sync_posts.metricool.matching import (
    normalize_text,
    post_networks,
    post_publication_datetime,
)
from metricool_sync_posts.timeutil import dates_equal_within_minutes


class ScheduleGuard:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS attempts "
            "(brand TEXT, page TEXT, PRIMARY KEY (brand, page))"
        )
        return db

    def contains(self, brand: str, page: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM attempts WHERE brand=? AND page=?", (brand, page)
            ).fetchone() is not None

    def reserve(self, brand: str, page: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "INSERT OR IGNORE INTO attempts VALUES (?, ?)", (brand, page)
            ).rowcount == 1


def exact_existing_post(
    posts: list[dict[str, Any]], caption: str, network: str, pub: datetime, tz: str
) -> bool:
    for post in posts:
        when = post_publication_datetime(post, tz)
        if (
            when is not None
            and dates_equal_within_minutes(pub, when, minutes=1)
            and network in post_networks(post)
            and normalize_text(caption) == normalize_text(str(post.get("text") or ""))
        ):
            return True
    return False
