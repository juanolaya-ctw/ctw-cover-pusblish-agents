"""Bogotá timezone helpers for Notion and Metricool date handling."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_in(tz_name: str) -> datetime:
    return datetime.now(tz(tz_name))


def calendar_week_bounds(reference: datetime, tz_name: str) -> tuple[datetime, datetime]:
    """Monday 00:00:00 through Sunday 23:59:59.999999 in the given timezone."""
    local = reference.astimezone(tz(tz_name))
    monday = local.date() - timedelta(days=local.weekday())
    start = datetime.combine(monday, datetime.min.time(), tzinfo=tz(tz_name))
    end = start + timedelta(days=7) - timedelta(microseconds=1)
    return start, end


def publication_window(center: date, tz_name: str, days: int) -> tuple[datetime, datetime]:
    """Inclusive window ±days around a calendar date in Bogotá."""
    zone = tz(tz_name)
    start = datetime.combine(center - timedelta(days=days), datetime.min.time(), tzinfo=zone)
    end = datetime.combine(center + timedelta(days=days), datetime.max.time(), tzinfo=zone)
    return start, end


def notion_date_to_datetime(value: date | datetime, tz_name: str) -> datetime:
    zone = tz(tz_name)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=zone)
        return value.astimezone(zone)
    return datetime.combine(value, datetime.min.time(), tzinfo=zone)


def dates_equal_within_minutes(a: datetime, b: datetime, minutes: int = 1) -> bool:
    if a.tzinfo is None or b.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return abs((a - b).total_seconds()) <= minutes * 60


def iso_metricool(dt: datetime) -> str:
    """ISO local datetime without offset (Metricool publicationDate.dateTime style)."""
    local = dt.astimezone(dt.tzinfo)
    return local.strftime("%Y-%m-%dT%H:%M:%S")
