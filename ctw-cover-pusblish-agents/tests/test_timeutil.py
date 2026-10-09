from datetime import date, datetime
from zoneinfo import ZoneInfo

from metricool_sync_posts.timeutil import (
    calendar_week_bounds,
    dates_equal_within_minutes,
    notion_date_to_datetime,
    publication_sort_key,
)


def test_calendar_week_monday_sunday_bogota():
    ref = datetime(2026, 10, 7, 15, 0, tzinfo=ZoneInfo("America/Bogota"))  # Wednesday
    start, end = calendar_week_bounds(ref, "America/Bogota")
    assert start.weekday() == 0
    assert start.hour == 0
    assert end.weekday() == 6
    assert end.hour == 23


def test_notion_date_naive_gets_bogota():
    d = datetime(2026, 10, 10, 10, 30)
    dt = notion_date_to_datetime(d, "America/Bogota")
    assert dt.tzinfo == ZoneInfo("America/Bogota")


def test_publication_sort_key_mixed_date_and_datetime():
    fallback = datetime(2026, 10, 1, 0, 0, tzinfo=ZoneInfo("America/Bogota"))
    bogota = ZoneInfo("America/Bogota")
    keys = [
        publication_sort_key(date(2026, 10, 5), fallback),
        publication_sort_key(datetime(2026, 10, 6, 12, 0, tzinfo=bogota), fallback),
    ]
    assert keys == sorted(keys)


def test_dates_within_one_minute():
    a = datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    b = datetime(2026, 1, 1, 12, 0, 30, tzinfo=ZoneInfo("America/Bogota"))
    assert dates_equal_within_minutes(a, b, minutes=1)
