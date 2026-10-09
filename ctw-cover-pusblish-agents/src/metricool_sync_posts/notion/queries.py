"""Notion database query filter builders."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def status_equals(prop_name: str, status: str) -> dict[str, Any]:
    return {
        "property": prop_name,
        "status": {"equals": status},
    }


def status_equals_select(prop_name: str, status: str) -> dict[str, Any]:
    """Fallback when Estado is a select instead of status type."""
    return {
        "property": prop_name,
        "select": {"equals": status},
    }


def publication_on_or_after(prop_name: str, iso_date: str) -> dict[str, Any]:
    return {"property": prop_name, "date": {"on_or_after": iso_date}}


def publication_on_or_before(prop_name: str, iso_date: str) -> dict[str, Any]:
    return {"property": prop_name, "date": {"on_or_before": iso_date}}


def and_filter(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"and": list(parts)}


def week_publication_filter(
    prop_publication: str,
    week_start: datetime,
    week_end: datetime,
) -> dict[str, Any]:
    return and_filter(
        publication_on_or_after(prop_publication, week_start.date().isoformat()),
        publication_on_or_before(prop_publication, week_end.date().isoformat()),
    )


def window_publication_filter(
    prop_publication: str,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    return and_filter(
        publication_on_or_after(prop_publication, window_start.date().isoformat()),
        publication_on_or_before(prop_publication, window_end.date().isoformat()),
    )
