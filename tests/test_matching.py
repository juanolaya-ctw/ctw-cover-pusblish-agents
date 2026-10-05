from datetime import datetime
from zoneinfo import ZoneInfo

from metricool_sync_posts.metricool.matching import find_best_match, post_state

TZ = "America/Bogota"


def test_find_best_match_by_caption_and_date():
    pub = datetime(2026, 10, 10, 18, 0, tzinfo=ZoneInfo(TZ))
    candidates = [
        {
            "id": "1",
            "text": "Hello world post",
            "publicationDate": {"dateTime": "2026-10-10T18:00:00", "timezone": TZ},
            "providers": [{"network": "instagram"}],
            "state": "PUBLISHED",
        },
        {
            "id": "2",
            "text": "Other",
            "publicationDate": {"dateTime": "2026-10-11T18:00:00", "timezone": TZ},
            "providers": [{"network": "instagram"}],
        },
    ]
    match = find_best_match(
        notion_caption="Hello world post",
        notion_channel="Instagram",
        notion_publication=pub,
        candidates=candidates,
        tz_name=TZ,
    )
    assert match is not None
    assert match["id"] == "1"
    assert post_state(match) == "PUBLISHED"
