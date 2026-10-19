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


def _publication_key(publication: datetime | None) -> str:
    """Minute precision so a new hour counts and seconds do not."""
    if publication is None:
        return ""
    return publication.replace(second=0, microsecond=0).isoformat()


class ScheduleGuard:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS attempts "
            "(brand TEXT, page TEXT, publication TEXT, PRIMARY KEY (brand, page))"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
        if "publication" not in columns:
            db.execute("ALTER TABLE attempts ADD COLUMN publication TEXT")
        return db

    def contains(self, brand: str, page: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM attempts WHERE brand=? AND page=?", (brand, page)
            ).fetchone() is not None

    def blocks(self, brand: str, page: str, publication: datetime) -> bool:
        """True when a previous attempt still owns this page at this time.

        A stored time that differs from ``publication`` does not block: the
        team moved Publicación. A row from before times were stored (empty
        publication) still blocks, until an expiry skip releases it.
        """
        stored = self._publication(brand, page)
        if stored is None:
            return False
        key = _publication_key(publication)
        if not stored or not key:
            return True
        return stored == key

    def reserve(self, brand: str, page: str, publication: datetime | None = None) -> bool:
        """Claim this page. A different publication replaces the old claim."""
        key = _publication_key(publication)
        with self._connect() as db:
            row = db.execute(
                "SELECT publication FROM attempts WHERE brand=? AND page=?",
                (brand, page),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO attempts (brand, page, publication) VALUES (?, ?, ?)",
                    (brand, page, key or None),
                )
                return True
            stored = row[0] or ""
            if key and stored and stored != key:
                db.execute(
                    "UPDATE attempts SET publication=? WHERE brand=? AND page=?",
                    (key, brand, page),
                )
                return True
            return False

    def release(self, brand: str, page: str) -> None:
        """Drop a claim. Used when the publication time has already passed."""
        with self._connect() as db:
            db.execute(
                "DELETE FROM attempts WHERE brand=? AND page=?",
                (brand, page),
            )

    def _publication(self, brand: str, page: str) -> str | None:
        """Stored time, or None when this page has no claim. Empty string is legacy."""
        with self._connect() as db:
            row = db.execute(
                "SELECT publication FROM attempts WHERE brand=? AND page=?",
                (brand, page),
            ).fetchone()
        if row is None:
            return None
        return row[0] or ""


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
