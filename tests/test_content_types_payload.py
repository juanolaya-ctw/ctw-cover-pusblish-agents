from datetime import datetime
from zoneinfo import ZoneInfo

from metricool_sync_posts.jobs.content_types import build_schedule_body, infer_instagram_type


def test_instagram_stories_type():
    assert infer_instagram_type("x", "Historias") == "STORIES"


def test_carousel_media_list():
    pub = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    body = build_schedule_body(
        caption="cap",
        publication=pub,
        tz_name="America/Bogota",
        channel="Instagram",
        title="Carrusel test",
        content_type="Carrusel",
        media_urls=["https://a.png", "https://b.png"],
    )
    assert body["instagramData"]["type"] == "CAROUSEL"
    assert body["media"] == ["https://a.png", "https://b.png"]


def test_instagram_cover_url_on_reel():
    pub = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    body = build_schedule_body(
        caption="cap",
        publication=pub,
        tz_name="America/Bogota",
        channel="Instagram",
        title="trials x",
        content_type="Reels",
        media_urls=["https://v.mp4"],
        cover_url="https://cover.jpg",
    )
    assert body["videoThumbnailUrl"] == "https://cover.jpg"
    assert "coverUrl" not in body["instagramData"]
    assert body["media"] == ["https://v.mp4"]
