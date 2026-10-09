"""Fixes from the 2026-10-09 dry-run against Metricool and Notion."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from metricool_sync_posts.config import Settings
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
    find_best_match,
    find_duplicate_candidates,
    find_existing_piece,
    find_sync_match,
    post_networks,
    post_state,
)
from metricool_sync_posts.notion.properties import _first_channel
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


def _run_schedule(tmp_path, rows, *, dry, posts=None, **settings_kw):
    settings = _settings(tmp_path, **settings_kw)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
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
    assert find_duplicate_candidates([near], **kwargs) == [near]
    assert find_duplicate_candidates([far], **kwargs) == []
    assert find_duplicate_candidates([other_net], **kwargs) == []


def test_fuzzy_caption_overlap_matches_without_the_same_prefix():
    notion = (
        "A Nico le tomó una década en el ecosistema llegar a sentarse con Simón Borrero"
    )
    edited = (
        "década ecosistema sentarse Simón pero el resto del copy cambió por completo en metricool"
    )
    post = _post(
        post_id=4,
        text=edited,
        when="2026-10-09T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    found = find_duplicate_candidates(
        [post],
        caption=notion,
        title="titulo que no aparece en metricool",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    )
    assert found == [post]

    weak = _post(
        post_id=5,
        text="otro texto que solo menciona sentarse y nada mas del original aqui",
        when="2026-10-09T12:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    assert find_duplicate_candidates(
        [weak],
        caption="sentarse con el equipo de colombia tech esta semana en bogota",
        title="otro titulo",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    ) == []


def test_ambiguous_duplicates_are_skipped_not_created(tmp_path):
    row = _sched_row(
        channel="Instagram",
        title="The bridge: Sentarse con un fundador unicornio",
        caption="copy distinto que no identifica ninguno de los dos posts por texto",
        publication=datetime(2026, 10, 9, 18, 0, tzinfo=BOG),
    )
    first = _post(
        post_id=11,
        text="primer texto pendiente en el mismo horario de instagram hoy",
        when="2026-10-09T18:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
    )
    second = _post(
        post_id=12,
        text="segundo texto pendiente tambien dentro de la ventana de quince minutos",
        when="2026-10-09T18:10:00",
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
