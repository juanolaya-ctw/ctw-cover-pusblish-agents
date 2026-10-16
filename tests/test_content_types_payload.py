from datetime import datetime
from zoneinfo import ZoneInfo

from metricool_sync_posts.jobs.content_types import (
    build_schedule_body,
    infer_instagram_type,
    schedule_bodies,
    tiktok_schedule_data,
)


def test_instagram_stories_type():
    assert infer_instagram_type("x", "Historias") == "STORY"
    assert infer_instagram_type("x", "Piezas estática, Historias") == "STORY"
    assert infer_instagram_type("x", "Stories") == "STORY"


def test_spanish_static_is_post_not_reel():
    assert infer_instagram_type("Todo sigue un proceso", "Piezas estática") == "POST"
    assert infer_instagram_type("x", "Pieza estatica") == "POST"
    assert infer_instagram_type("x", "Estático") == "POST"
    assert infer_instagram_type("x", "Carrusel") == "POST"
    assert infer_instagram_type("x", "Reels - Tik Tok - Shorts") == "REEL"


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
    assert body["instagramData"]["type"] == "POST"
    assert body["media"] == ["https://a.png", "https://b.png"]
    assert "CAROUSEL" not in str(body)


def test_youtube_title_is_the_hook_not_the_task_title():
    pub = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    hook = "H" * 120
    body = build_schedule_body(
        caption="caption que no debe publicarse como titulo",
        publication=pub,
        tz_name="America/Bogota",
        channel="YouTube",
        title="Trials: Cursos Google",
        content_type="Video Largo",
        media_urls=["https://v.mp4"],
        youtube_title=hook,
    )
    assert body["youtubeData"]["type"] == "video"
    assert body["youtubeData"]["title"] == "H" * 100
    assert "Trials" not in body["youtubeData"]["title"]
    assert "caption" not in body["youtubeData"]["title"]

    short = build_schedule_body(
        caption="otro caption",
        publication=pub,
        tz_name="America/Bogota",
        channel="Youtube Shorts",
        title="Trials: Cursos Google",
        content_type="Video Largo",
        media_urls=["https://v.mp4"],
        networks=["youtube"],
        youtube_short=True,
        youtube_title="Cursos de Google en un minuto",
    )
    assert short["youtubeData"]["type"] == "short"
    assert short["youtubeData"]["title"] == "Cursos de Google en un minuto"


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
    assert schedule_bodies(body) == [body]


def _pub():
    return datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("America/Bogota"))


def test_instagram_reel_cover_is_unchanged_without_youtube():
    body = build_schedule_body(
        caption="cap",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="Instagram, TikTok",
        title="hook",
        content_type="Reels",
        media_urls=["https://v.mp4"],
        cover_url="https://cover.jpg",
        networks=["instagram", "tiktok"],
    )
    posts = schedule_bodies(body)
    assert len(posts) == 1
    assert posts[0]["videoThumbnailUrl"] == "https://cover.jpg"
    assert [item["network"] for item in posts[0]["providers"]] == ["instagram", "tiktok"]


def test_youtube_only_never_sends_a_thumbnail():
    body = build_schedule_body(
        caption="cap",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="YouTube",
        title="hook",
        content_type="Video Largo",
        media_urls=["https://v.mp4"],
        cover_url="https://cover.jpg",
        youtube_title="Hook publico",
        networks=["youtube"],
    )
    body["videoThumbnailUrl"] = "https://cover.jpg"
    body["youtubeData"]["thumbnail"] = "https://cover.jpg"
    body["videoCoverMilliseconds"] = 1500
    posts = schedule_bodies(body)
    assert len(posts) == 1
    youtube = posts[0]
    assert youtube["providers"] == [{"network": "youtube"}]
    assert youtube["youtubeData"]["type"] == "video"
    assert youtube["youtubeData"]["title"] == "Hook publico"
    assert "thumbnail" not in youtube["youtubeData"]
    assert "videoThumbnailUrl" not in youtube
    assert "videoCoverMilliseconds" not in youtube


def test_combined_reel_splits_youtube_off_the_cover():
    """Swagger has one videoThumbnailUrl for the whole post, so YouTube is separate."""
    body = build_schedule_body(
        caption="cap",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="Instagram, Youtube Shorts, TikTok",
        title="hook",
        content_type="Reels",
        media_urls=["https://v.mp4"],
        cover_url="https://static.metricool.com/cover.jpg",
        networks=["instagram", "tiktok", "youtube"],
        youtube_short=True,
        youtube_title="Hook del short",
    )
    body["youtubeData"]["thumbnailUrl"] = "https://static.metricool.com/cover.jpg"
    posts = schedule_bodies(body)
    assert len(posts) == 2
    instagram, youtube = posts
    assert [item["network"] for item in instagram["providers"]] == ["instagram", "tiktok"]
    assert instagram["videoThumbnailUrl"] == "https://static.metricool.com/cover.jpg"
    assert instagram["instagramData"]["type"] == "REEL"
    assert "youtubeData" not in instagram
    assert youtube["providers"] == [{"network": "youtube"}]
    assert youtube["youtubeData"]["type"] == "short"
    assert youtube["youtubeData"]["title"] == "Hook del short"
    assert "thumbnailUrl" not in youtube["youtubeData"]
    assert "videoThumbnailUrl" not in youtube
    assert "instagramData" not in youtube
    assert "tiktokData" not in youtube
    assert instagram["autoPublish"] is True
    assert instagram["tiktokData"] == tiktok_schedule_data(photo=False)
    assert instagram["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"
    assert youtube["media"] == ["https://v.mp4"]
    assert instagram["media"] == ["https://v.mp4"]


def test_youtube_without_a_cover_stays_on_the_same_post():
    body = build_schedule_body(
        caption="cap",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="Youtube Shorts, TikTok",
        title="hook",
        content_type="Reels",
        media_urls=["https://v.mp4"],
        networks=["youtube", "tiktok"],
        youtube_short=True,
        youtube_title="Hook",
    )
    body["youtubeData"]["thumbnail"] = "https://cover.jpg"
    posts = schedule_bodies(body)
    assert len(posts) == 1
    assert [item["network"] for item in posts[0]["providers"]] == ["youtube", "tiktok"]
    assert "videoThumbnailUrl" not in posts[0]
    assert "thumbnail" not in posts[0]["youtubeData"]
    assert posts[0]["youtubeData"]["type"] == "short"
    assert posts[0]["autoPublish"] is True
    assert posts[0]["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"
    assert posts[0]["tiktokData"]["disableDuet"] is False
