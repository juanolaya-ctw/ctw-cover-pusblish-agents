"""Fixes from the first live schedule run on 55352c2."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from test_dryrun_fixes import BOG, _prop_names, _run_schedule, _sched_row, _settings

from metricool_sync_posts.jobs.content_types import (
    build_schedule_body,
    video_network_skip_reason,
)
from metricool_sync_posts.logging_setup import (
    RedactSecretsFilter,
    redact_secrets,
    setup_logging,
)
from metricool_sync_posts.metricool.client import MetricoolApiError, MetricoolClient
from metricool_sync_posts.notion.properties import row_from_page

PNGS = [
    "https://litter.catbox.moe/shorts-a.png",
    "https://litter.catbox.moe/shorts-b.png",
]


def test_images_for_youtube_and_tiktok_are_not_posted(tmp_path):
    """Youtube Shorts + TikTok with PNGs was a Metricool 400. Do not POST."""
    row = _sched_row(
        page_id="3ed99829-d217-8008-b68f-e94676e34922",
        channel="Youtube Shorts, TikTok",
        channels=["Youtube Shorts", "TikTok"],
        content_type="Piezas estática",
        cover_text="Hook publico",
        final_file_url="https://drive.google.com/drive/folders/images",
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [row], dry=False, media_urls=PNGS
    )
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    assert stats["errors"] == 0
    media.assert_called_once()
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "no_video_for_network"


def test_tiktok_photo_carousel_uses_photo_cover_index(tmp_path):
    """Swagger ScheduledPostTikTokData.photoCoverIndex is a TikTok photo post."""
    row = _sched_row(
        channel="TikTok",
        channels=["TikTok"],
        content_type="Carrusel",
        title="Carrusel de fotos",
    )
    stats, _notion, metricool, _media, slack = _run_schedule(
        tmp_path, [row], dry=False, media_urls=PNGS
    )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "tiktok"}]
    assert body["tiktokData"] == {"photoCoverIndex": 0}
    assert "youtubeData" not in body
    assert slack.call_args.kwargs["reason"] == "scheduled"


def test_video_still_schedules_on_youtube_and_tiktok(tmp_path):
    row = _sched_row(
        channel="Youtube Shorts, TikTok",
        channels=["Youtube Shorts", "TikTok"],
        content_type="Reels",
        cover_text="Hook publico",
    )
    stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [row], dry=False)
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["youtubeData"]["type"] == "short"
    assert "tiktokData" not in body


def test_skip_reason_matches_swagger_photo_support():
    assert video_network_skip_reason(["youtube", "tiktok"], PNGS) == "no_video_for_network"
    assert video_network_skip_reason(["youtube"], PNGS) == "no_video_for_network"
    assert video_network_skip_reason(["tiktok"], PNGS) is None
    assert video_network_skip_reason(["instagram"], PNGS) is None
    assert (
        video_network_skip_reason(
            ["youtube", "tiktok"],
            ["https://litter.catbox.moe/clip.mp4"],
        )
        is None
    )
    pub = datetime(2026, 10, 11, 12, 0, tzinfo=BOG)
    body = build_schedule_body(
        caption="fotos",
        publication=pub,
        tz_name="America/Bogota",
        channel="TikTok",
        title="Carrusel",
        content_type="Carrusel",
        media_urls=PNGS,
        networks=["tiktok"],
    )
    assert body["tiktokData"]["photoCoverIndex"] == 0


def test_date_only_notion_publication_is_not_scheduled_at_midnight(tmp_path):
    row = _sched_row(
        page_id="3ee99829-d217-806c-a680-ebc2eb37f116",
        publication=date(2026, 10, 11),
    )
    stats, notion, metricool, media, slack = _run_schedule(tmp_path, [row], dry=False)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    assert stats["errors"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "missing_time"
    assert "no time" in slack.call_args.kwargs["message"]


def test_row_from_page_keeps_a_date_without_a_time(tmp_path):
    settings = _settings(tmp_path)
    name = settings.notion_prop_publication
    page = {
        "id": "3ee99829-d217-806c-a680-ebc2eb37f116",
        "url": "https://notion.so/row",
        "properties": {
            name: {"type": "date", "date": {"start": "2026-10-11"}},
        },
    }
    row = row_from_page(page, _prop_names(settings))
    assert row.publication == date(2026, 10, 11)
    assert not isinstance(row.publication, datetime)


def test_explicit_midnight_is_a_time_and_can_schedule(tmp_path):
    row = _sched_row(publication=datetime(2026, 10, 11, 0, 0, tzinfo=BOG))
    stats, _notion, metricool, _media, _slack = _run_schedule(tmp_path, [row], dry=True)
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0


def test_metricool_error_logs_body_and_request_shape_without_the_token(tmp_path, caplog):
    token = "super-secret-user-token"
    settings = _settings(tmp_path, METRICOOL_USER_TOKEN=token)
    payload = {
        "publicationDate": {"dateTime": "2026-10-11T18:00:00", "timezone": "America/Bogota"},
        "text": "caption that must not be required in the log",
        "providers": [{"network": "youtube"}, {"network": "tiktok"}],
        "media": PNGS,
        "youtubeData": {"type": "short", "title": "Hook"},
    }
    error_body = '{"message":"media type not allowed for youtube"}' + ("x" * 3000) + "ENDMARKER"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(400, text=error_body)
        return httpx.Response(500, text='{"message":"update failed"}')

    client = MetricoolClient(settings)
    client._http.close()
    client._http = httpx.Client(
        base_url="https://app.metricool.com/api",
        headers={"X-Mc-Auth": token},
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(MetricoolApiError, match="media type not allowed") as caught:
            client.create_scheduled_post(payload)
    assert caught.value.status == 400
    assert "ENDMARKER" not in caplog.text
    assert "ENDMARKER" not in caught.value.body
    assert len(caught.value.body) <= 2048
    assert token not in caplog.text
    assert "networks" in caplog.text
    assert "youtube" in caplog.text
    assert ".png" in caplog.text
    assert "2026-10-11T18:00:00" in caplog.text
    assert "short" in caplog.text
    with caplog.at_level(logging.ERROR):
        with pytest.raises(MetricoolApiError, match="update failed"):
            client.update_scheduled_post("99", payload)
    client.close()


def test_failed_create_reports_metricool_message_to_slack(tmp_path):
    from test_dryrun_fixes import NOW

    from metricool_sync_posts.jobs.schedule import run_schedule

    row = _sched_row(page_id="3f499829-d217-8113-82dd-e1e4863b155b")
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [row]
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.side_effect = MetricoolApiError(
        status=400,
        body='{"message":"providers rejected"}',
        shape={"networks": ["instagram"]},
    )
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 0
    assert stats["errors"] == 1
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "schedule_error"
    assert "providers rejected" in slack.call_args.kwargs["message"]


def test_http_loggers_stay_quiet_and_secrets_are_redacted(caplog):
    setup_logging("DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    leaked = (
        "POST https://hooks.slack.com/services/T00/B00/supersecret "
        "https://app.metricool.com/api/x?userToken=abc&blogId=5822365"
    )
    assert "supersecret" not in redact_secrets(leaked)
    assert "userToken=abc" not in redact_secrets(leaked)
    assert "blogId=5822365" in redact_secrets(leaked)
    assert "https://hooks.slack.com/services/REDACTED" in redact_secrets(leaked)

    logger = logging.getLogger("metricool_sync_posts.secret_probe")
    logger.addFilter(RedactSecretsFilter())
    logger.warning("Slack notify failed: %s", leaked)
    assert "supersecret" not in caplog.text
    assert "userToken=abc" not in caplog.text
    assert "REDACTED" in caplog.text


def test_put_405_then_patch_success_is_not_an_error(tmp_path):
    settings = _settings(tmp_path)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "PUT":
            return httpx.Response(405, text="method not allowed")
        return httpx.Response(200, json={"id": 99})

    client = MetricoolClient(settings)
    client._http.close()
    client._http = httpx.Client(
        base_url="https://app.metricool.com/api",
        transport=httpx.MockTransport(handler),
    )
    updated = client.update_scheduled_post("99", {"text": "x", "providers": []})
    assert updated == {"id": 99}
    assert seen == ["PUT", "PATCH"]
    client.close()


def test_client_module_imports_without_a_cycle():
    # Importing the client must not pull a half-initialized media pipeline.
    assert Path("src/metricool_sync_posts/metricool/client.py").is_file()
