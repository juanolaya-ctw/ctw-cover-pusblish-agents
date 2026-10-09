"""Fixes from the 2026-10-09 dry-run against Metricool and Notion."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.common import caption_has_placeholder
from metricool_sync_posts.jobs.confirm_published import run_confirm_published
from metricool_sync_posts.jobs.content_types import build_schedule_body
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.jobs.sync_dates import run_sync_dates
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.metricool.channels import (
    label_is_youtube_short,
    normalize_channel,
    plan_canals,
)
from metricool_sync_posts.metricool.client import FORBIDDEN_BLOG_IDS, MetricoolClient
from metricool_sync_posts.metricool.matching import (
    caption_similarity,
    find_best_match,
    find_duplicate_candidates,
    find_existing_piece,
    find_sync_match,
    post_networks,
    post_state,
)
from metricool_sync_posts.notion.properties import _first_channel, row_from_page
from metricool_sync_posts.slack.notify import notify_slack

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
TRUORA = "Truora ya hizo algo que muchas startups siguen sin animarse a copiar"


def _settings(tmp_path, **extra):
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID="5822365",
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        MEDIA_WORK_DIR=tmp_path / "media",
        SLACK_DEDUPE_FILE=tmp_path / "slack.json",
    )
    data.update(extra)
    return Settings(**data)


def _post(
    *,
    post_id,
    text,
    when,
    providers,
    uuid="uuid-1",
    twitter=None,
    media=None,
    youtube_title=None,
):
    body = {
        "id": post_id,
        "uuid": uuid,
        "text": text,
        "publicationDate": {"dateTime": when, "timezone": TZ},
        "providers": providers,
        "twitterData": {} if twitter is None else twitter,
    }
    if media is not None:
        body["media"] = media
    if youtube_title is not None:
        body["youtubeData"] = {"title": youtube_title}
    return body


def _sched_row(**overrides):
    base = dict(
        page_id="3e499829-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/row",
        channel="Instagram",
        title="The Bridge México",
        caption="cinco cosas que amamos de méxico y que el equipo repite en cada pieza",
        final_file_url="https://cdn.example/video.mp4",
        content_type="Reels",
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
        metricool_id=None,
        metricool_uuid=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_schedule(tmp_path, rows, *, dry, posts=None, linked=None, **settings_kw):
    settings = _settings(tmp_path, **settings_kw)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    notion.fetch_linked_in_window.return_value = linked or []
    metricool.get_scheduled_posts.return_value = posts or []
    metricool.create_scheduled_post.return_value = {"id": 99}
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ) as media,
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, media, slack


def test_calendar_query_uses_start_and_end_not_from_to():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [{"id": 1}]})

    client = MetricoolClient(_settings(Path("/tmp/ctw-metricool-tests")))
    client._http.close()
    client._http = httpx.Client(
        base_url="https://app.metricool.com/api",
        transport=httpx.MockTransport(handler),
    )
    posts = client.get_scheduled_posts(
        datetime(2026, 10, 2, 0, 0, tzinfo=BOG),
        datetime(2026, 10, 16, 23, 59, tzinfo=BOG),
    )
    assert posts == [{"id": 1}]
    assert seen["params"]["start"].startswith("2026-10-02T00:00:00")
    assert seen["params"]["end"].startswith("2026-10-16T23:59:00")
    assert "from" not in seen["params"]
    assert "to" not in seen["params"]
    assert seen["params"]["blogId"] == "5822365"
    client.close()


@pytest.mark.parametrize("blog_id", sorted(FORBIDDEN_BLOG_IDS))
def test_refuses_other_brands(blog_id):
    with pytest.raises(RuntimeError, match="5822365"):
        MetricoolClient(_settings(Path("/tmp/ctw-metricool-tests"), METRICOOL_BLOG_ID=blog_id))


def test_provider_status_and_networks_ignore_empty_twitter_data():
    post = _post(
        post_id=391714178,
        text="si los cargos de marketing fueran completamente precisos",
        when="2026-10-09T09:00:00",
        providers=[
            {"network": "instagram", "status": "PUBLISHED", "detailedStatus": "Published"},
        ],
        twitter={"text": "stub that must not count as a network"},
    )
    assert post_networks(post) == {"instagram"}
    assert post_state(post) == "PUBLISHED"

    mixed = _post(
        post_id=2,
        text="x",
        when="2026-10-09T09:00:00",
        providers=[
            {"network": "instagram", "status": "PUBLISHED"},
            {"network": "tiktok", "status": "PENDING"},
        ],
    )
    assert post_state(mixed) == "PENDING"
    assert post_networks(mixed) == {"instagram", "tiktok"}

    failed = _post(
        post_id=3,
        text="x",
        when="2026-10-09T09:00:00",
        providers=[
            {"network": "instagram", "status": "PUBLISHED"},
            {"network": "youtube", "status": "ERROR"},
        ],
    )
    assert post_state(failed) == "ERROR"

    publishing = _post(
        post_id=4,
        text="x",
        when="2026-10-09T09:00:00",
        providers=[{"network": "instagram", "status": "PUBLISHING"}],
    )
    assert post_state(publishing) == "PENDING"

    legacy = {
        "text": "Hello world post",
        "providers": [{"network": "instagram"}],
        "state": "PUBLISHED",
        "twitterData": {},
    }
    assert post_state(legacy) == "PUBLISHED"
    assert "twitter" not in post_networks(legacy)
    assert post_state({"providers": [{"network": "instagram"}]}) == "UNKNOWN"


def test_sync_match_ignores_clock_and_prefers_stored_id():
    drifted = _post(
        post_id=501,
        text=TRUORA,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    notion_at_noon = datetime(2026, 10, 11, 12, 0, tzinfo=BOG)
    assert find_best_match(TRUORA, "Instagram", notion_at_noon, [drifted], TZ) is None
    match = find_sync_match(
        notion_caption=TRUORA,
        notion_title="Carrusel: The bridge Truora",
        notion_channel="Instagram",
        notion_publication=notion_at_noon,
        candidates=[drifted],
        tz_name=TZ,
    )
    assert match is not None
    assert match["id"] == 501

    other = _post(
        post_id=9,
        text=TRUORA,
        when="2026-10-11T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stored = _post(
        post_id=388227164,
        text="unrelated caption that should lose to the stored id",
        when="2026-10-01T08:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="abc-uuid",
    )
    by_id = find_sync_match(
        notion_caption=TRUORA,
        notion_title="Truora",
        notion_channel="Instagram",
        notion_publication=notion_at_noon,
        candidates=[other, stored],
        tz_name=TZ,
        metricool_id="388227164",
    )
    assert by_id is not None
    assert by_id["id"] == 388227164


def test_existing_piece_matches_caption_title_or_media():
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    post = _post(
        post_id=388904158,
        text=caption,
        when="2026-10-05T15:23:00",
        providers=[{"network": "instagram", "status": "PUBLISHED"}],
    )
    found = find_existing_piece(
        [post],
        caption=caption,
        title="The Bridge México",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 5, 15, 0, tzinfo=BOG),
    )
    assert found is not None
    assert found["id"] == 388904158

    titled = _post(
        post_id=77,
        text="body copy that does not repeat the notion title at all here today",
        when="2026-10-08T18:00:00",
        providers=[{"network": "youtube", "status": "PENDING"}],
        youtube_title="Pulso Cap. 2 — Poncho de los Ríos — Video Largo",
    )
    by_title = find_existing_piece(
        [titled],
        caption="different caption that should not be the reason for this match",
        title="Pulso Cap. 2 — Poncho de los Ríos — Video Largo",
        network="youtube",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 8, 14, 0, tzinfo=BOG),
    )
    assert by_title is not None and by_title["id"] == 77

    linked = _post(
        post_id=88,
        text="another unrelated caption for the media overlap case in this test",
        when="2026-10-06T18:00:00",
        providers=[{"network": "youtube", "status": "PUBLISHED"}],
        media=["https://youtu.be/ssk7BVyhvSw"],
    )
    by_media = find_existing_piece(
        [linked],
        caption="no shared words with the metricool text in any meaningful way",
        title="AI Summit",
        network="youtube",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 6, 18, 0, tzinfo=BOG),
        media_urls=["https://youtu.be/ssk7BVyhvSw"],
    )
    assert by_media is not None and by_media["id"] == 88


def test_linkedin_is_stripped_not_scheduled_alone():
    assert normalize_channel("IG/LinkedIn") == "instagram"
    assert normalize_channel("LinkedIn CT") == "linkedin"
    assert normalize_channel("LinkedIn Majo") == "linkedin"
    props = {
        "Canal": {
            "type": "multi_select",
            "multi_select": [{"name": "Instagram"}, {"name": "LinkedIn"}],
        }
    }
    assert _first_channel(props, "Canal") == "Instagram"
    body = build_schedule_body(
        caption="cap",
        publication=datetime(2026, 10, 10, 15, tzinfo=BOG),
        tz_name=TZ,
        channel="IG/LinkedIn",
        title="Todo sigue un proceso",
        content_type="Piezas estática",
        media_urls=["https://cdn.example/a.png"],
        cover_url="https://cdn.example/c.jpg",
    )
    assert body["providers"] == [{"network": "instagram"}]
    assert body["instagramData"]["type"] == "POST"
    assert "linkedinData" not in body
    assert "videoThumbnailUrl" not in body
    with pytest.raises(ValueError, match="Miniatura"):
        build_schedule_body(
            caption="cap",
            publication=datetime(2026, 10, 10, 15, tzinfo=BOG),
            tz_name=TZ,
            channel="YouTube",
            title="Pulso miniatura",
            content_type="Miniaturas",
            media_urls=["https://cdn.example/thumb.jpg"],
        )


def test_past_rows_are_skipped_and_do_not_consume_slots(tmp_path):
    past = _sched_row(
        page_id="3e299829-d217-807a-b26b-e7f7c9b86e97",
        publication=datetime(2026, 10, 5, 15, 0, tzinfo=BOG),
        caption="una pieza vieja que no debe salir ahora mas cinco minutos",
    )
    future = _sched_row(
        page_id="3f199829-d217-81c0-a4b9-f4be3b9ac102",
        publication=datetime(2026, 10, 10, 18, 0, tzinfo=BOG),
    )
    extra = _sched_row(
        page_id="3f499829-d217-8113-82dd-e1e4863b155b",
        publication=datetime(2026, 10, 11, 9, 0, tzinfo=BOG),
        caption="otra pieza futura que queda fuera del cupo de este corrido",
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path,
        [past, future, extra],
        dry=False,
        SCHEDULE_MAX_PER_RUN=1,
    )
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_called_once()
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["publicationDate"]["dateTime"].startswith("2026-10-10T18:00:00")
    notion.set_status.assert_called_once_with(future.page_id, "Programado", dry_run=False)
    assert media.call_count == 1
    reasons = [call.kwargs["reason"] for call in slack.call_args_list]
    assert "past_publication_date" in reasons


def test_duplicate_sets_notion_instead_of_creating(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    row = _sched_row(
        publication=datetime(2026, 10, 5, 15, 0, tzinfo=BOG),
        caption=caption,
    )
    published = _post(
        post_id=388904158,
        text=caption,
        when="2026-10-05T15:23:00",
        providers=[{"network": "instagram", "status": "PUBLISHED"}],
    )
    stats, notion, metricool, media, _slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[published]
    )
    assert stats["scheduled"] == 0
    assert stats["reconciled"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_called_once_with(row.page_id, "Publicado", dry_run=False)

    pending = _post(
        post_id=42,
        text=caption,
        when="2026-10-10T15:40:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    future = _sched_row(publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG), caption=caption)
    stats, notion, metricool, _media, _slack = _run_schedule(
        tmp_path, [future], dry=False, posts=[pending]
    )
    notion.set_status.assert_called_once_with(future.page_id, "Programado", dry_run=False)
    metricool.create_scheduled_post.assert_not_called()
    assert stats["reconciled"] == 1


def test_duplicate_error_does_not_change_notion(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    row = _sched_row(caption=caption)
    failed = _post(
        post_id=7,
        text=caption,
        when="2026-10-10T15:00:00",
        providers=[{"network": "instagram", "status": "ERROR"}],
    )
    stats, notion, metricool, _media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[failed]
    )
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "metricool_duplicate_error"


def test_dry_run_duplicate_does_not_write(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    row = _sched_row(caption=caption)
    pending = _post(
        post_id=42,
        text=caption,
        when="2026-10-10T15:40:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stats, notion, metricool, _media, _slack = _run_schedule(
        tmp_path, [row], dry=True, posts=[pending]
    )
    assert stats["reconciled"] == 1
    notion.set_status.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_create_failure_leaves_notion(tmp_path):
    settings = _settings(tmp_path)
    row = _sched_row()
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [row]
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.side_effect = RuntimeError("metricool down")
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack"),
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["errors"] == 1
    assert stats["scheduled"] == 0
    notion.set_status.assert_not_called()


def test_notion_failure_is_reconciled_from_metricool_without_reposting(tmp_path):
    """Create succeeded and Notion did not. The next run must not POST again."""
    settings = _settings(tmp_path)
    row = _sched_row()
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [row]
    notion.set_status.side_effect = RuntimeError("notion down")
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.return_value = {"id": 55}

    def _run(notion_client, metricool_client):
        with (
            patch(
                "metricool_sync_posts.jobs.schedule.NotionRepository",
                return_value=notion_client,
            ),
            patch(
                "metricool_sync_posts.jobs.schedule.MetricoolClient",
                return_value=metricool_client,
            ),
            patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
            patch(
                "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
                return_value=["https://cdn.example/video.mp4"],
            ),
            patch("metricool_sync_posts.jobs.schedule.notify_slack"),
        ):
            return run_schedule(settings=settings, dry_run=False)

    first = _run(notion, metricool)
    assert first["errors"] == 1
    assert first["scheduled"] == 0
    metricool.create_scheduled_post.assert_called_once()

    notion2, metricool2 = MagicMock(), MagicMock()
    notion2.fetch_approved_current_week.return_value = [row]
    metricool2.get_scheduled_posts.return_value = [
        _post(
            post_id=55,
            text=row.caption,
            when="2026-10-10T15:00:00",
            providers=[{"network": "instagram", "status": "PENDING"}],
        )
    ]
    second = _run(notion2, metricool2)
    assert second["reconciled"] == 1
    assert second["scheduled"] == 0
    metricool2.create_scheduled_post.assert_not_called()
    notion2.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)


def test_successful_create_marks_programado(tmp_path):
    row = _sched_row(channel="Instagram / LinkedIn", content_type="Piezas estática")
    stats, notion, metricool, _media, _slack = _run_schedule(tmp_path, [row], dry=False)
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "instagram"}]
    assert body["instagramData"]["type"] == "POST"
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)


def test_miniatura_and_empty_media_are_not_scheduled(tmp_path):
    thumb = _sched_row(
        page_id="3e599829-d217-81ae-b705-f2157804d819",
        channel="YouTube",
        content_type="Miniaturas",
        final_file_url="",
        title="Pulso Cap. 2 — Poncho de los Ríos — Miniatura",
        caption="texto largo de la miniatura que no debe volverse un video",
    )
    empty = _sched_row(
        page_id="3f099829-d217-818c-925d-cfb5304410e7",
        content_type="Reels",
        final_file_url="  ",
        caption="reel sin archivo final no puede salir a metricool hoy",
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [thumb, empty], dry=False
    )
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 2
    assert stats["errors"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    media.assert_not_called()
    reasons = {call.kwargs["reason"] for call in slack.call_args_list}
    assert reasons == {"miniatura_not_a_post", "missing_media"}


def test_ig_nico_and_linkedin_canals_never_reach_media(tmp_path):
    rows = [
        _sched_row(page_id="3e999829-d217-81f0-adad-dcd75070ff19", channel="IG Nico"),
        _sched_row(page_id="3f099829-d217-8136-818f-fd268438643d", channel="LinkedIn CT"),
        _sched_row(page_id="3f199829-d217-816b-9594-df2ea9d49e0b", channel="Instagram"),
    ]
    stats, _notion, metricool, media, _slack = _run_schedule(
        tmp_path,
        rows,
        dry=True,
        SCHEDULE_EXCLUDE_CHANNELS="",
    )
    assert stats["queried"] == 1
    assert stats["scheduled"] == 1
    assert media.call_count == 1
    metricool.create_scheduled_post.assert_not_called()


def test_duplicates_do_not_consume_the_create_slot(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    dup = _sched_row(
        page_id="3ed99829-d217-8098-bab4-da756040c155",
        caption=caption,
    )
    fresh = _sched_row(
        page_id="3c799829-d217-80c9-9470-e92d4b5a1fe7",
        caption="un texto nuevo que todavía no existe en el calendario de metricool",
        publication=datetime(2026, 10, 11, 15, 30, tzinfo=BOG),
    )
    pending = _post(
        post_id=391714178,
        text=caption,
        when="2026-10-10T16:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stats, notion, metricool, _media, _slack = _run_schedule(
        tmp_path,
        [dup, fresh],
        dry=False,
        posts=[pending],
        SCHEDULE_MAX_PER_RUN=1,
    )
    assert stats["reconciled"] == 1
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_called_once()
    created_for = metricool.create_scheduled_post.call_args.args[0]["text"]
    assert "texto nuevo" in created_for
    assert notion.set_status.call_count == 2


def _programado(page_id, caption, when, **extra):
    row = dict(
        page_id=page_id,
        url=f"https://notion.so/{page_id}",
        channel="Instagram",
        title=extra.pop("title", "titulo"),
        caption=caption,
        publication=when,
        metricool_id=extra.pop("metricool_id", None),
        metricool_uuid=None,
    )
    row.update(extra)
    return SimpleNamespace(**row)


def test_confirm_uses_provider_status_and_vanished_rule(tmp_path):
    published_text = "b2b b2c d2c caption larga que identifica esta pieza de instagram"
    pending_text = TRUORA
    error_text = "error piece caption that should block and not flip to publicado now"
    unknown_text = "unknown status caption still in the calendar so it must stay programado"
    gone_text = "gone from metricool entirely and the publication time has passed already"
    future_text = "future unmatched piece that should stay untouched for this run now"
    posts = [
        _post(
            post_id=1,
            text=published_text,
            when="2026-10-08T12:00:00",
            providers=[{"network": "instagram", "status": "PUBLISHED"}],
        ),
        _post(
            post_id=2,
            text=pending_text,
            when="2026-10-11T15:00:00",
            providers=[{"network": "instagram", "status": "PENDING"}],
        ),
        _post(
            post_id=3,
            text=error_text,
            when="2026-10-08T09:00:00",
            providers=[
                {"network": "instagram", "status": "PUBLISHED"},
                {"network": "tiktok", "status": "ERROR"},
            ],
        ),
        _post(
            post_id=4,
            text=unknown_text,
            when="2026-10-08T18:00:00",
            providers=[{"network": "instagram"}],
        ),
    ]
    rows = [
        _programado("page-pub", published_text, datetime(2026, 10, 8, 12, tzinfo=BOG)),
        _programado("page-pend", pending_text, datetime(2026, 10, 11, 12, tzinfo=BOG)),
        _programado("page-err", error_text, datetime(2026, 10, 8, 9, tzinfo=BOG)),
        _programado("page-unk", unknown_text, datetime(2026, 10, 8, 18, tzinfo=BOG)),
        _programado("page-gone", gone_text, datetime(2026, 10, 7, 9, tzinfo=BOG)),
        _programado("page-future", future_text, datetime(2026, 10, 12, 18, tzinfo=BOG)),
    ]
    settings = _settings(tmp_path, SLACK_WEBHOOK_URL="https://hooks.slack.test/abc")
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = rows
    metricool.get_scheduled_posts.return_value = posts
    with (
        patch(
            "metricool_sync_posts.jobs.confirm_published.NotionRepository",
            return_value=notion,
        ),
        patch(
            "metricool_sync_posts.jobs.confirm_published.MetricoolClient",
            return_value=metricool,
        ),
        patch("metricool_sync_posts.jobs.confirm_published.now_in", return_value=NOW),
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
    ):
        stats = run_confirm_published(settings=settings, dry_run=False)
    assert stats["published"] == 2  # real PUBLISHED + vanished
    assert stats["pending"] == 2  # PENDING drift + UNKNOWN
    assert stats["blockers"] == 1
    assert stats["skipped"] == 1
    marked = {call.args[0] for call in notion.set_status.call_args_list}
    assert marked == {"page-pub", "page-gone"}
    for call in notion.set_status.call_args_list:
        assert call.args[1] == "Publicado"
        assert call.kwargs["dry_run"] is False
    slack_post.assert_called_once()


def test_confirm_dry_run_does_not_write(tmp_path):
    text = "b2b b2c d2c caption larga que identifica esta pieza de instagram"
    row = _programado("page-pub", text, datetime(2026, 10, 8, 12, tzinfo=BOG))
    post = _post(
        post_id=1,
        text=text,
        when="2026-10-08T12:00:00",
        providers=[{"network": "instagram", "status": "PUBLISHED"}],
    )
    settings = _settings(tmp_path, SLACK_WEBHOOK_URL="https://hooks.slack.test/abc")
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]
    with (
        patch(
            "metricool_sync_posts.jobs.confirm_published.NotionRepository",
            return_value=notion,
        ),
        patch(
            "metricool_sync_posts.jobs.confirm_published.MetricoolClient",
            return_value=metricool,
        ),
        patch("metricool_sync_posts.jobs.confirm_published.now_in", return_value=NOW),
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
    ):
        stats = run_confirm_published(settings=settings, dry_run=True)
    assert stats["published"] == 1
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    slack_post.assert_not_called()
    assert not (tmp_path / "slack.json").exists()


def test_sync_dates_updates_drifted_notion_time(tmp_path):
    row = _programado(
        "page-truora",
        TRUORA,
        datetime(2026, 10, 11, 12, tzinfo=BOG),
        title="Carrusel: The bridge Truora",
    )
    post = _post(
        post_id=610,
        text=TRUORA,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="truora-uuid",
    )
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]

    def _run(dry):
        with (
            patch("metricool_sync_posts.jobs.sync_dates.NotionRepository", return_value=notion),
            patch("metricool_sync_posts.jobs.sync_dates.MetricoolClient", return_value=metricool),
            patch("metricool_sync_posts.jobs.sync_dates.now_in", return_value=NOW),
        ):
            return run_sync_dates(settings=settings, dry_run=dry)

    dry_stats = _run(True)
    assert dry_stats["updated"] == 1
    metricool.update_scheduled_post.assert_not_called()

    live_stats = _run(False)
    assert live_stats["updated"] == 1
    metricool.update_scheduled_post.assert_called_once()
    args = metricool.update_scheduled_post.call_args
    assert args.args[0] == "610"
    assert args.args[1]["publicationDate"]["dateTime"] == "2026-10-11T12:00:00"
    assert args.kwargs["uuid"] == "truora-uuid"


def test_sync_dates_skips_published(tmp_path):
    text = "ya publicada y no se le mueve la fecha aunque notion diga otra hora"
    row = _programado("page-done", text, datetime(2026, 10, 8, 12, tzinfo=BOG))
    post = _post(
        post_id=1,
        text=text,
        when="2026-10-08T15:00:00",
        providers=[{"network": "instagram", "status": "PUBLISHED"}],
    )
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]
    with (
        patch("metricool_sync_posts.jobs.sync_dates.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.sync_dates.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.sync_dates.now_in", return_value=NOW),
    ):
        stats = run_sync_dates(settings=settings, dry_run=False)
    assert stats["updated"] == 0
    assert stats["skipped"] == 1
    metricool.update_scheduled_post.assert_not_called()


def test_dry_run_slack_does_not_post_or_touch_dedupe(tmp_path):
    dedupe = MagicMock()
    with patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post:
        notify_slack(
            webhook_url="https://hooks.slack.test/abc",
            channel="#ct-growth",
            dedupe=dedupe,
            notion_page_id="page",
            reason="past_publication_date",
            message="skip",
            dry_run=True,
        )
    slack_post.assert_not_called()
    dedupe.should_notify.assert_not_called()
    assert not (tmp_path / "slack.json").exists()


def test_dry_run_media_does_not_upload(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    called = {"upload": 0}

    def _boom(*_args, **_kwargs):
        called["upload"] += 1
        raise AssertionError("upload")

    monkeypatch.setattr(
        "metricool_sync_posts.media.pipeline.upload_public_url",
        _boom,
    )
    url = prepare_media_for_metricool(
        settings=settings,
        metricool=MagicMock(),
        source_url="https://cdn.example/clip.mp4",
        dry_run=True,
    )
    assert url == "https://cdn.example/clip.mp4"
    assert called["upload"] == 0
    assert not (tmp_path / "media").exists()


def test_youtube_shorts_label_maps_to_youtube_short():
    assert normalize_channel("Youtube Shorts") == "youtube"
    assert normalize_channel("YT Shorts") == "youtube"
    assert label_is_youtube_short("Youtube Shorts")
    assert label_is_youtube_short("youtube shorts")
    assert label_is_youtube_short("YoUtUbE sHoRtS")
    assert label_is_youtube_short("YT Shorts")
    assert not label_is_youtube_short("YouTube")
    assert not label_is_youtube_short("YT")

    shorts_first = plan_canals(["Youtube Shorts", "TikTok"])
    assert shorts_first.networks == ("youtube", "tiktok")
    assert shorts_first.youtube_short is True
    assert shorts_first.skip_nico is False

    tiktok_first = plan_canals(["TiKToK", "youtube shorts"])
    assert tiktok_first.networks == ("tiktok", "youtube")
    assert tiktok_first.youtube_short is True

    mixed = plan_canals(["Instagram", "LinkedIn", "Newsletter"])
    assert mixed.networks == ("instagram",)
    assert mixed.youtube_short is False

    nico = plan_canals(["IG Nico", "TikTok"])
    assert nico.skip_nico is True
    assert nico.networks == ()


def test_multi_canal_schedules_one_post_to_every_network(tmp_path):
    row = _sched_row(
        channel=None,
        channels=["Youtube Shorts", "TikTok"],
        title="4 libros de negocios",
        content_type="Video Largo",
        publication=datetime(2026, 10, 10, 18, 0, tzinfo=BOG),
        caption="cuatro libros de negocios que el equipo recomienda esta semana",
    )
    stats, notion, metricool, _media, _slack = _run_schedule(tmp_path, [row], dry=True)
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    body = metricool.create_scheduled_post.call_args
    assert body is None
    # dry-run does not create; rebuild the body the job would have sent via the log path
    # by running live so the payload is asserted on create_scheduled_post.
    stats, notion, metricool, _media, _slack = _run_schedule(tmp_path, [row], dry=False)
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "youtube"}, {"network": "tiktok"}]
    assert body["youtubeData"]["type"] == "short"
    assert "instagramData" not in body
    assert "linkedinData" not in body
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)

    flipped = _sched_row(
        page_id="3f299829-d217-814e-a5ec-d856e45ce5e4",
        channel=None,
        channels=["TikTok", "Youtube Shorts"],
        title="GovTech: dBrain+",
        content_type="Video Largo",
        caption="govtech dbrain un caso para contar en corto y en tiktok juntos",
    )
    stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [flipped], dry=False)
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "tiktok"}, {"network": "youtube"}]
    assert body["youtubeData"]["type"] == "short"

    cased = _sched_row(
        page_id="3f399829-d217-814e-a5ec-d856e45ce5e5",
        channel=None,
        channels=["YoUtUbE sHoRtS", "TiKToK"],
        title="casing",
        content_type="Video Largo",
        caption="el mismo mapeo tiene que aguantar mayusculas mezcladas en canal",
    )
    _stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [cased], dry=False)
    body = metricool.create_scheduled_post.call_args.args[0]
    assert [item["network"] for item in body["providers"]] == ["youtube", "tiktok"]
    assert body["youtubeData"]["type"] == "short"


def test_multi_canal_drops_linkedin_and_newsletter_and_skips_ig_nico(tmp_path):
    mixed = _sched_row(
        channel=None,
        channels=["Instagram", "LinkedIn"],
        content_type="Piezas estática",
        caption="instagram se queda y linkedin no entra en el mismo post",
    )
    stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [mixed], dry=False)
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "instagram"}]
    assert "linkedinData" not in body

    newsletter = _sched_row(
        page_id="3f499829-d217-814e-a5ec-d856e45ce5e6",
        channel=None,
        channels=["Newsletter", "YouTube"],
        content_type="Video Largo",
        caption="youtube largo se programa y el newsletter se queda manual",
    )
    _stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [newsletter], dry=False)
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "youtube"}]
    assert body["youtubeData"]["type"] == "video"

    nico = _sched_row(
        page_id="3f599829-d217-814e-a5ec-d856e45ce5e7",
        channel=None,
        channels=["IG Nico", "Instagram"],
        caption="si aparece ig nico no se publica ninguna de las otras redes",
    )
    stats, notion, metricool, media, _slack = _run_schedule(tmp_path, [nico], dry=False)
    assert stats["queried"] == 0
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()


def test_same_slot_is_a_duplicate_even_when_caption_was_edited(tmp_path):
    """Notion copy and the Metricool caption diverged; the slot is still the same post."""
    notion_caption = (
        "The bridge: entrevista con un fundador que construyo durante anos antes de este cruce"
    )
    row = _sched_row(
        channel="Instagram",
        title="The bridge: Sentarse con un fundador unicornio",
        caption=notion_caption,
        publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
        content_type="Reels",
    )
    existing = _post(
        post_id=88001,
        text="A Nico le tomó una década en el ecosistema llegar a sentarse con Simón",
        when="2026-10-09T18:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stats, notion, metricool, media, _slack = _run_schedule(
        tmp_path, [row], dry=True, posts=[existing]
    )
    assert stats["reconciled"] == 1
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()

    stats, notion, metricool, _media, _slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[existing]
    )
    assert stats["reconciled"] == 1
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)


def test_slot_window_is_fifteen_minutes_and_same_network():
    notion_at = datetime(2026, 10, 9, 18, 0, tzinfo=BOG)
    caption = "copy que no comparte palabras con el texto que ya vive en metricool xyz"
    near = _post(
        post_id=1,
        text="A Nico le tomó una década en el ecosistema llegar a sentarse con Simón",
        when="2026-10-09T18:14:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    far = _post(
        post_id=2,
        text="A Nico le tomó una década en el ecosistema llegar a sentarse con Simón",
        when="2026-10-09T18:16:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    other_net = _post(
        post_id=3,
        text="A Nico le tomó una década en el ecosistema llegar a sentarse con Simón",
        when="2026-10-09T18:00:00",
        providers=[{"network": "youtube", "status": "PENDING"}],
    )
    kwargs = dict(
        caption=caption,
        title="titulo distinto sin solape",
        network="instagram",
        tz_name=TZ,
        notion_publication=notion_at,
    )
    # Same slot with a clearly different caption is not the same piece.
    assert find_duplicate_candidates([near], **kwargs) == []
    assert find_duplicate_candidates([far], **kwargs) == []
    assert find_duplicate_candidates([other_net], **kwargs) == []
    related = _post(
        post_id=4,
        text=caption + " y un cierre que metricool acorto",
        when="2026-10-09T18:10:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates([related], **kwargs) == [related]


_IA_CAPTION = (
    "La IA nos va a reemplazar solo si dejamos de aprender a usarla en el trabajo diario. "
    "Este carrusel recorre tres oficios que cambian cuando el modelo escribe el primer borrador "
    "y el equipo tiene que decidir qué sigue siendo humano. "
    "#ColombiaTech #InteligenciaArtificial https://colombia.tech/ia 🤖"
)
_CLAUDE_CAPTION = (
    "Cinco atajos de Claude que el equipo usa para investigar, resumir y escribir más rápido. "
    "No son trucos de internet: son el flujo real de una redacción que publica todas las semanas. "
    "#ColombiaTech #Claude https://colombia.tech/claude ✨"
)
_HAVI_CAPTION = (
    "Havi Nguyen construyó Abby Care para acompañar a familias que cuidan a alguien mayor. "
    "La startup combina operación clínica y software, y por eso el equipo crece de otra forma. "
    "#ColombiaTech #Startups https://colombia.tech/abby"
)
_SMARTFIT_TITLE = "Expandir la Suscripción fuera de las Paredes: Smartfit"
_SMARTFIT_BODY = (
    "Smartfit dejó de vender solo el acceso a las sedes y llevó la suscripción a otros momentos "
    "del día. En esta conversación el equipo de Colombia Tech recorre precios, retención y la "
    "expansión regional del modelo. No es un carrusel de tips: es cómo una marca de bienestar "
    "sale de sus paredes y sigue cobrando cuando la persona ya no está en el gimnasio."
)
_SHARED_BOILERPLATE = (
    "La suscripción también aparece cuando una startup cobra por el equipo y no por la sede. "
    "Colombia Tech arma este carrusel para contar otra expansión, la de una herramienta de "
    "inteligencia artificial que cambia el trabajo diario de varios oficios en la región. "
    "#ColombiaTech https://colombia.tech/fondo 🚀"
)


def test_caption_similarity_is_high_for_edits_and_strict_when_dates_are_far():
    notion = (
        "A Nico le tomó una década en el ecosistema llegar a sentarse con Simón Borrero "
        "para hablar de cómo se construye una compañía desde cero"
    )
    edited = (
        "A Nico le tomó una década en el ecosistema llegar a sentarse con Simón "
        "pero después la charla giró hacia el fundraising y los inversionistas "
        "#ColombiaTech https://colombia.tech/bridge 🎙️"
    )
    assert 0.6 <= caption_similarity(notion, edited) < 0.85
    close = _post(
        post_id=4,
        text=edited,
        when="2026-10-09T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates(
        [close],
        caption=notion,
        title="titulo que no aparece en metricool",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    ) == [close]

    # Same edit a week apart is not close enough: far dates need a near-copy.
    distant = _post(
        post_id=6,
        text=edited,
        when="2026-10-04T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates(
        [distant],
        caption=notion,
        title="titulo que no aparece en metricool",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    ) == []
    exact_far = _post(
        post_id=7,
        text=notion,
        when="2026-10-04T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates(
        [exact_far],
        caption=notion,
        title="otro titulo",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    ) == [exact_far]


def test_shared_spanish_words_are_not_the_same_post():
    """Different CTW captions share boilerplate. That must not look like a duplicate."""
    assert caption_similarity(_IA_CAPTION, _CLAUDE_CAPTION) < 0.6
    assert caption_similarity(_IA_CAPTION, _HAVI_CAPTION) < 0.6
    assert caption_similarity(_IA_CAPTION, _SHARED_BOILERPLATE) < 0.6
    assert caption_similarity(_CLAUDE_CAPTION, _SHARED_BOILERPLATE) < 0.6
    assert caption_similarity(_SMARTFIT_BODY, _SHARED_BOILERPLATE) < 0.6
    assert caption_similarity(_SMARTFIT_TITLE, _SMARTFIT_TITLE) == 1.0

    decoy = _post(
        post_id=50,
        text=_SHARED_BOILERPLATE,
        when="2026-10-10T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates(
        [decoy],
        caption=_IA_CAPTION,
        title="Carrusel: La IA nos va a reemplazar",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 10, 9, 0, tzinfo=BOG),
    ) == []


def test_new_pieces_schedule_while_real_duplicates_reconcile(tmp_path):
    bridge = _sched_row(
        page_id="3e599829-d217-8054-a250-ee6e9300fe25",
        channel="Instagram",
        title="The bridge: Sentarse con un fundador unicornio",
        caption=(
            "A Nico le tomó años construir el puente para sentarse con un fundador que ya "
            "pasó por la escala. Esta conversación recorre la duda, el equipo y la decisión "
            "de seguir. No es un recuento de métricas."
        ),
        publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
        content_type="Reels",
    )
    smartfit = _sched_row(
        page_id="3ea99829-d217-81c3-a7af-ddb880308a36",
        channel="YouTube",
        title=_SMARTFIT_TITLE,
        caption=_SMARTFIT_BODY,
        publication=datetime(2026, 10, 8, 18, 0, tzinfo=BOG),
        content_type="Video Largo",
    )
    ia = _sched_row(
        page_id="3ed99829-d217-8008-b68f-e94676e34922",
        channel="Instagram",
        title="Carrusel: La IA nos va a reemplazar",
        caption=_IA_CAPTION,
        publication=datetime(2026, 10, 10, 9, 0, tzinfo=BOG),
        content_type="Carrusel",
    )
    trials = _sched_row(
        page_id="3ea99829-d217-80de-913f-e21ea1dfde5a",
        channel="Instagram",
        title="Trials: 5 secret Claude cheat code",
        caption=_CLAUDE_CAPTION,
        publication=datetime(2026, 10, 10, 15, 30, tzinfo=BOG),
        content_type="Reels",
    )
    havi = _sched_row(
        page_id="3f499829-d217-8113-82dd-e1e4863b155b",
        channel="Instagram",
        title="Havi Nguyen y Abby Care",
        caption=_HAVI_CAPTION,
        publication=datetime(2026, 10, 11, 9, 0, tzinfo=BOG),
        content_type="Carrusel",
    )
    posts = [
        _post(
            post_id=88001,
            text="A Nico le tomó una década en el ecosistema llegar a sentarse con Simón",
            when="2026-10-09T18:00:00",
            providers=[{"network": "instagram", "status": "PENDING"}],
        ),
        _post(
            post_id=391622734,
            text=(
                "Smartfit llevó la suscripción fuera del gimnasio. Colombia Tech habla con "
                "el equipo sobre expansión, precios y retención."
            ),
            when="2026-10-08T18:00:00",
            providers=[{"network": "youtube", "status": "PUBLISHED"}],
            youtube_title=_SMARTFIT_TITLE,
        ),
        _post(
            post_id=111,
            text=_SHARED_BOILERPLATE,
            when="2026-10-10T11:00:00",
            providers=[{"network": "instagram", "status": "PENDING"}],
        ),
        _post(
            post_id=112,
            text=(
                "Bogotá recibe el GovTech Summit y este carrusel explica por qué la ciudad "
                "se volvió casa de compras públicas digitales. El equipo recorre la agenda, "
                "los fondos y las startups que llegan esa semana. #ColombiaTech #GovTech"
            ),
            when="2026-10-07T09:00:00",
            providers=[{"network": "instagram", "status": "PUBLISHED"}],
        ),
        _post(
            post_id=113,
            text=(
                "Inteligencia artificial, startups y el equipo de Colombia Tech en otro video "
                "sobre cómo construir una compañía con suscripción y expansión regional."
            ),
            when="2026-10-06T18:00:00",
            providers=[{"network": "youtube", "status": "PUBLISHED"}],
            youtube_title="Otra marca también habla de suscripción y de expansión",
        ),
    ]
    stats, notion, metricool, _media, slack = _run_schedule(
        tmp_path, [smartfit, bridge, ia, trials, havi], dry=False, posts=posts
    )
    assert stats["scheduled"] == 3
    assert stats["reconciled"] == 2
    assert stats["skipped"] == 0
    assert metricool.create_scheduled_post.call_count == 3
    reasons = {call.kwargs["reason"] for call in slack.call_args_list}
    assert "ambiguous_duplicate" not in reasons
    status = {call.args[0]: call.args[1] for call in notion.set_status.call_args_list}
    assert status[bridge.page_id] == "Programado"
    assert status[smartfit.page_id] == "Publicado"
    assert status[ia.page_id] == "Programado"
    assert status[trials.page_id] == "Programado"
    assert status[havi.page_id] == "Programado"


def test_ambiguous_duplicates_are_skipped_not_created(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    row = _sched_row(
        channel="Instagram",
        title="The bridge México",
        caption=caption,
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
    )
    first = _post(
        post_id=11,
        text=caption,
        when="2026-10-10T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    second = _post(
        post_id=12,
        text=caption + " con un parrafo extra que no cambia la pieza",
        when="2026-10-10T15:10:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[first, second]
    )
    assert stats["scheduled"] == 0
    assert stats["reconciled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "ambiguous_duplicate"


_OCDE_CAPTION = (
    "Colombia quedó fuera del top 10 de gobierno digital de la OCDE y este carrusel "
    "explica qué mide el ranking y por qué el país perdió puestos este año."
)
_TRUORA_CAPTION = (
    "Truora ya hizo algo que muchas startups colombianas siguen sin animarse a copiar"
)


def _prop_names(settings):
    return {
        "status": settings.notion_prop_status,
        "publication": settings.notion_prop_publication,
        "channel": settings.notion_prop_channel,
        "caption": settings.notion_prop_caption,
        "final_file": settings.notion_prop_final_file,
        "title": settings.notion_prop_title,
        "content_type": settings.notion_prop_content_type,
        "miniatura": settings.notion_prop_miniatura,
        "protagonista": settings.notion_prop_protagonista,
        "metricool_id": settings.notion_prop_metricool_id,
        "metricool_uuid": settings.notion_prop_metricool_uuid,
    }


def test_real_ocde_payload_is_not_reconciled_to_truora(tmp_path, caplog):
    """Exact Notion page and Metricool post from the 3692b0a dry-run.

    Shared CTA words (comenta / enviamos / información) must not make this a match.
    """
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "ocde-vs-truora.json").read_text()
    )
    row = row_from_page(payload["notion_page"], _prop_names(_settings(tmp_path)))
    post = payload["metricool_post"][0]
    assert row.page_id == "3f299829-d217-81cf-83ee-e66e8ef5139b"
    assert post["id"] == 387636704
    with caplog.at_level("INFO"):
        stats, notion, metricool, media, slack = _run_schedule(
            tmp_path, [row], dry=True, posts=[post], linked=[]
        )
    assert stats["reconciled"] == 0
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    notion.set_status.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "slot_conflict"
    audit = [
        line
        for line in caplog.text.splitlines()
        if "387636704" in line and "caption_score=" in line
    ]
    assert audit
    assert "reason=none" in audit[0]
    assert "overlap=-" in audit[0]


def test_placeholders_are_detected_without_flagging_spanish_todo():
    assert caption_has_placeholder("Faltan [X] días para el episodio")
    assert caption_has_placeholder("Entrevista con [NOMBRE] en el bridge")
    assert caption_has_placeholder("El recurso está en [link]")
    assert caption_has_placeholder("Publicar el {fecha} cuando esté listo")
    assert caption_has_placeholder("Cierre XXX")
    assert caption_has_placeholder("TODO: escribir el cierre")
    assert caption_has_placeholder("fecha TBD")
    assert caption_has_placeholder("lorem ipsum dolor sit amet")
    assert not caption_has_placeholder("Todo el equipo publicó esta semana en Colombia")
    assert not caption_has_placeholder(_OCDE_CAPTION)


def test_placeholder_caption_is_skipped(tmp_path):
    row = _sched_row(
        page_id="3f099829-d217-81aa-aaaa-bbbbccccdddd",
        channel="Instagram",
        title="Building in public cap 1: The Bridge con MariRoms",
        caption="Faltan [X] días para sentarnos con MariRoms y contar el primer capítulo.",
        publication=datetime(2026, 10, 12, 18, 0, tzinfo=BOG),
    )
    stats, notion, metricool, media, slack = _run_schedule(tmp_path, [row], dry=False)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    assert stats["reconciled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "placeholder_caption"


def test_govtech_ocde_is_not_reconciled_to_the_truora_post(tmp_path):
    """Dry-run of 9c3c9e6 set 3f299829 to Programado off Metricool 387636704.

    Character ratio on the long Spanish captions is about 0.3, which is not the
    same piece. The Programado Truora row does not own that post by caption, so
    the claim set cannot be what saves the reconcile.
    """
    ocde = (
        "Colombia quedó fuera del top 10 de gobierno digital de la OCDE y este carrusel "
        "explica qué mide el ranking y por qué el país perdió puestos este año. "
        "El equipo de Colombia Tech recorre los indicadores, la brecha con los líderes "
        "y lo que el sector público puede cambiar antes de la próxima medición."
    )
    truora_metricool = (
        "Truora ya hizo algo que muchas startups colombianas siguen sin animarse a copiar. "
        "Expandirse a México no es llevar el mismo producto: es reconstruir confianza, "
        "compliance y el equipo comercial desde cero mientras el mercado pide pruebas."
    )
    truora_notion = (
        "El carrusel arma el mapa de oficinas, socios y tiempos del salto regional. "
        "No repite el guion del reel: lista decisiones, riesgos y el orden de llegada."
    )
    assert caption_similarity(ocde, truora_metricool) >= 0.3
    govtech = _sched_row(
        page_id="3f299829-d217-81cf-83ee-e66e8ef5139b",
        channel="Instagram",
        title="GovTech | Colombia cayó en el ranking de gobierno digital de la OCDE",
        caption=ocde,
        publication=datetime(2026, 10, 11, 15, 0, tzinfo=BOG),
        content_type="Carrusel",
        final_file_url="https://drive.google.com/drive/folders/1L9DJnRPO4FqEsgIPRaW1B5hUiykRKYjr",
    )
    truora_post = _post(
        post_id=387636704,
        text=truora_metricool,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    truora_row = _sched_row(
        page_id="3e699829-d217-80a7-9a14-e93f91cf2070",
        channel="Instagram",
        title="Carrusel: The bridge Truora",
        caption=truora_notion,
        publication=datetime(2026, 10, 11, 12, 0, tzinfo=BOG),
        status="Programado",
        content_type="Carrusel",
        metricool_id=None,
        metricool_uuid=None,
    )
    assert find_duplicate_candidates(
        [truora_post],
        caption=truora_notion,
        title=truora_row.title,
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 11, 12, 0, tzinfo=BOG),
    ) == []
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [govtech], dry=True, posts=[truora_post], linked=[truora_row]
    )
    assert stats["scheduled"] == 0
    assert stats["reconciled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "slot_conflict"


def test_programado_row_claims_post_by_id_or_uuid(tmp_path):
    """A stored Metricool id/uuid on a Programado row blocks a second reconcile."""
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    approved = _sched_row(
        page_id="3f299829-d217-81cf-83ee-e66e8ef5139b",
        caption=caption,
        publication=datetime(2026, 10, 11, 15, 0, tzinfo=BOG),
    )
    pending = _post(
        post_id=387636704,
        text=caption,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="truora-uuid",
    )
    published = _post(
        post_id=387636704,
        text=caption,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PUBLISHED"}],
        uuid="truora-uuid",
    )
    by_id = _sched_row(
        page_id="3e699829-d217-80a7-9a14-e93f91cf2070",
        title="Carrusel: The bridge Truora",
        caption="otro texto que no es el caption de metricool para esta pieza",
        publication=datetime(2026, 10, 11, 12, 0, tzinfo=BOG),
        status="Programado",
        metricool_id="387636704",
    )
    by_uuid = _sched_row(
        page_id="3e699829-d217-80a7-9a14-e93f91cf2070",
        title="Carrusel: The bridge Truora",
        caption="otro texto que no es el caption de metricool para esta pieza",
        publication=datetime(2026, 10, 11, 12, 0, tzinfo=BOG),
        status="Publicado",
        metricool_id=None,
        metricool_uuid="truora-uuid",
    )
    for linked, post in ((by_id, pending), (by_uuid, published)):
        stats, notion, metricool, media, slack = _run_schedule(
            tmp_path, [approved], dry=False, posts=[post], linked=[linked]
        )
        assert stats["reconciled"] == 0
        assert stats["scheduled"] == 0
        notion.set_status.assert_not_called()
        metricool.create_scheduled_post.assert_not_called()
        media.assert_not_called()
        assert slack.call_args.kwargs["reason"] == "slot_conflict"


def test_claimed_post_is_not_reused_for_a_later_row(tmp_path):
    caption = "cinco cosas que amamos de méxico y que el equipo repite en cada pieza"
    first = _sched_row(
        page_id="3ed99829-d217-8098-bab4-da756040c155",
        caption=caption,
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
    )
    second = _sched_row(
        page_id="3f199829-d217-81c0-a4b9-f4be3b9ac102",
        channel="Instagram",
        title="Otra fila en el mismo horario",
        caption="Un texto distinto sobre impuestos y planes institucionales para empresas.",
        publication=datetime(2026, 10, 10, 15, 5, tzinfo=BOG),
    )
    pending = _post(
        post_id=42,
        text=caption,
        when="2026-10-10T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [first, second], dry=False, posts=[pending]
    )
    assert stats["reconciled"] == 1
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_called_once_with(first.page_id, "Programado", dry_run=False)
    reasons = [call.kwargs["reason"] for call in slack.call_args_list]
    assert "slot_conflict" in reasons
