from datetime import datetime
from zoneinfo import ZoneInfo

from metricool_sync_posts.jobs.content_types import publication_format_groups
from metricool_sync_posts.metricool.matching import (
    FORMAT_INSTAGRAM_FEED,
    FORMAT_INSTAGRAM_STORY,
    FORMAT_TIKTOK,
    FORMAT_YOUTUBE,
    find_best_match,
    find_slot_occupants,
    post_format_groups,
    post_state,
)

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


def _metricool_post(post_id, when, network, ig_type=None):
    body = {
        "id": post_id,
        "text": "otro texto que no identifica esta pieza",
        "publicationDate": {"dateTime": when, "timezone": TZ},
        "providers": [{"network": network, "status": "PENDING"}],
    }
    if ig_type is not None:
        body["instagramData"] = {"type": ig_type, "autoPublish": True}
    return body


def test_publication_format_groups_split_stories_from_feed():
    assert publication_format_groups(
        ["instagram"], title="Historia fiscal", content_type="Historias"
    ) == {FORMAT_INSTAGRAM_STORY}
    assert publication_format_groups(
        ["instagram"],
        title="GovTech",
        content_type="Piezas estática, Historias",
    ) == {FORMAT_INSTAGRAM_STORY}
    assert publication_format_groups(
        ["instagram"], title="Ranking OCDE", content_type="Carrusel"
    ) == {FORMAT_INSTAGRAM_FEED}
    assert publication_format_groups(
        ["instagram"], title="Pieza estatica", content_type="Piezas estática"
    ) == {FORMAT_INSTAGRAM_FEED}
    assert publication_format_groups(
        ["instagram"], title="El puente", content_type="Reels"
    ) == {FORMAT_INSTAGRAM_FEED}
    assert publication_format_groups(
        ["instagram"], title="Trials: cheat codes", content_type="Reels"
    ) == {FORMAT_INSTAGRAM_FEED}
    assert publication_format_groups(
        ["tiktok"], title="Clip", content_type="Reels"
    ) == {FORMAT_TIKTOK}
    assert publication_format_groups(
        ["youtube"], title="Video largo", content_type="Video Largo"
    ) == {FORMAT_YOUTUBE}
    assert publication_format_groups(
        ["instagram", "tiktok", "youtube"],
        title="El puente",
        content_type="Reels",
    ) == {FORMAT_INSTAGRAM_FEED, FORMAT_TIKTOK, FORMAT_YOUTUBE}
    assert publication_format_groups(
        ["instagram", "tiktok"],
        title="Historia",
        content_type="Historias",
    ) == {FORMAT_INSTAGRAM_STORY, FORMAT_TIKTOK}


def test_metricool_format_groups_use_network_and_instagram_type():
    assert post_format_groups(_metricool_post(1, "2026-10-10T15:00:00", "instagram", "STORY")) == {
        FORMAT_INSTAGRAM_STORY
    }
    for ig_type in ("POST", "REEL", "TRIAL_REEL", "CAROUSEL"):
        assert post_format_groups(
            _metricool_post(2, "2026-10-10T15:00:00", "instagram", ig_type)
        ) == {FORMAT_INSTAGRAM_FEED}
    # No type: Instagram still occupies the feed slot.
    assert post_format_groups(_metricool_post(3, "2026-10-10T15:00:00", "instagram")) == {
        FORMAT_INSTAGRAM_FEED
    }
    assert post_format_groups(_metricool_post(4, "2026-10-10T15:00:00", "tiktok")) == {
        FORMAT_TIKTOK
    }
    youtube = _metricool_post(5, "2026-10-10T15:00:00", "youtube")
    youtube["youtubeData"] = {"type": "short", "title": "Hook"}
    assert post_format_groups(youtube) == {FORMAT_YOUTUBE}
    mixed = _metricool_post(6, "2026-10-10T15:00:00", "instagram", "REEL")
    mixed["providers"].append({"network": "tiktok", "status": "PENDING"})
    assert post_format_groups(mixed) == {FORMAT_INSTAGRAM_FEED, FORMAT_TIKTOK}


def test_slot_occupants_require_the_same_format_group_inside_fifteen_minutes():
    at = datetime(2026, 10, 10, 15, 0, tzinfo=ZoneInfo(TZ))
    story = _metricool_post(1, "2026-10-10T15:00:00", "instagram", "STORY")
    reel = _metricool_post(2, "2026-10-10T15:10:00", "instagram", "REEL")
    post = _metricool_post(3, "2026-10-10T15:15:00", "instagram", "POST")
    outside = _metricool_post(4, "2026-10-10T15:16:00", "instagram", "POST")
    tiktok = _metricool_post(5, "2026-10-10T15:00:00", "tiktok")
    youtube = _metricool_post(6, "2026-10-10T15:00:00", "youtube")
    candidates = [story, reel, post, outside, tiktok, youtube]
    kwargs = dict(network=None, tz_name=TZ, notion_publication=at)

    assert find_slot_occupants(
        candidates, networks=["instagram"], format_groups={FORMAT_INSTAGRAM_STORY}, **kwargs
    ) == [story]
    feed_hits = find_slot_occupants(
        candidates, networks=["instagram"], format_groups={FORMAT_INSTAGRAM_FEED}, **kwargs
    )
    assert feed_hits == [reel, post]
    assert outside not in feed_hits
    assert find_slot_occupants(
        candidates, networks=["tiktok"], format_groups={FORMAT_TIKTOK}, **kwargs
    ) == [tiktok]
    assert find_slot_occupants(
        candidates, networks=["youtube"], format_groups={FORMAT_YOUTUBE}, **kwargs
    ) == [youtube]
    assert find_slot_occupants(
        candidates,
        networks=["instagram", "tiktok"],
        format_groups={FORMAT_INSTAGRAM_STORY, FORMAT_TIKTOK},
        **kwargs,
    ) == [story, tiktok]
