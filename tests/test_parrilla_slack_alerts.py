"""Programado-without-Metricool and manual Reel-without-cover Slack alerts."""

from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.confirm_published import run_confirm_published
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard
from metricool_sync_posts.metricool.matching import is_instagram_reel_post, post_has_cover
from metricool_sync_posts.slack.digest import SlackDigest

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
# Monday morning, before the fictitious 08:58 slot.
NOW = datetime(2026, 10, 19, 7, 0, tzinfo=BOG)
BLOG = "5822365"
PAGE = "3f899829-d217-80ab-878b-c136b3b87ad4"
NOTION_URL = "https://www.notion.so/3f899829d21780ab878bc136b3b87ad4"
TITLE = "Vale la pena invertir en SpaceX"
CAPTION = (
    "Vale la pena invertir en SpaceX cuando el dinero puede quedarse quieto "
    "varios años y el riesgo de la compañía cabe en el portafolio."
)
SLOT = datetime(2026, 10, 19, 8, 58, tzinfo=BOG)
POST_ID = 400060750
IG_LINK = "https://www.instagram.com/reel/spacexclip/"
WEBHOOK = "https://hooks.slack.com/services/T000/B000/fixture"


def _settings(tmp_path, **extra):
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID=BLOG,
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        MEDIA_WORK_DIR=tmp_path / "media",
        SLACK_DEDUPE_FILE=tmp_path / "slack.json",
        SLACK_WEBHOOK_URL=WEBHOOK,
        SLACK_DEDUPE_HOURS=24,
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    data.update(extra)
    return Settings(**data)


def _row(page_id, title, when, **extra):
    status = extra.pop("status", "Programado")
    row = dict(
        page_id=page_id,
        url=extra.pop("url", NOTION_URL if page_id == PAGE else f"https://www.notion.so/{page_id}"),
        channel="Instagram",
        channels=["Instagram"],
        title=title,
        caption=extra.pop("caption", CAPTION if page_id == PAGE else f"{title} caption larga"),
        publication=when,
        status=status,
        content_type="Reels",
        final_file_url=None,
        metricool_id=None,
        metricool_uuid=None,
        cover_text="Hook",
    )
    row.update(extra)
    return SimpleNamespace(**row)


def _spacex(**extra):
    return _row(PAGE, TITLE, extra.pop("publication", SLOT), **extra)


def _reel(post_id=POST_ID, **extra):
    body = {
        "id": post_id,
        "uuid": f"uuid-{post_id}",
        "text": CAPTION,
        "publicationDate": {"dateTime": "2026-10-19T08:58:00", "timezone": TZ},
        "providers": [
            {
                "network": "instagram",
                "status": extra.pop("provider_status", "PENDING"),
                "publicUrl": extra.pop("public_url", IG_LINK),
            }
        ],
        "instagramData": {"type": extra.pop("ig_type", "REEL"), "autoPublish": True},
    }
    body.update(extra)
    return body


def _texts(slack_post) -> list[str]:
    return [call.kwargs["json"]["text"] for call in slack_post.call_args_list]


def _run_schedule(tmp_path, rows, posts, *, dry=False, settings=None):
    settings = settings or _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = []
    notion.fetch_linked_in_window.return_value = rows
    metricool.get_scheduled_posts.return_value = posts
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
    ):
        slack_post.return_value.status_code = 200
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, slack_post


def _run_confirm(tmp_path, rows, posts, *, dry=False):
    settings = _settings(tmp_path)
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
        slack_post.return_value.status_code = 200
        stats = run_confirm_published(settings=settings, dry_run=dry)
    return stats, notion, metricool, slack_post


def _age_dedupe(path, *, seconds: float) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    for value in raw.values():
        value["ts"] = time.time() - seconds
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_cover_field_is_video_thumbnail_or_a_chosen_frame():
    """Cover upload writes videoThumbnailUrl. A positive frame offset also counts."""
    bare = {"instagramData": {"type": "REEL"}}
    assert is_instagram_reel_post(bare) is True
    assert post_has_cover(bare) is False
    assert post_has_cover({**bare, "videoThumbnailUrl": ""}) is False
    assert post_has_cover({**bare, "videoCoverMilliseconds": 0}) is False
    assert post_has_cover({**bare, "videoCoverMilliseconds": "0"}) is False
    assert post_has_cover({**bare, "videoCoverMilliseconds": None}) is False
    assert post_has_cover({**bare, "videoThumbnailUrl": "https://static.metricool.com/c.jpg"})
    assert post_has_cover({**bare, "videoCoverMilliseconds": 1500})
    assert post_has_cover({**bare, "videoCoverMilliseconds": "1500"})
    nested = {"instagramData": {"type": "REEL", "videoThumbnailUrl": "https://c.jpg"}}
    assert post_has_cover(nested) is True
    assert is_instagram_reel_post({"instagramData": {"type": "POST"}}) is False
    assert is_instagram_reel_post({"instagramData": {"type": "TRIAL_REEL"}}) is True


def test_digest_keeps_one_shared_fix_and_adds_the_new_lines():
    digest = SlackDigest(webhook_url=WEBHOOK, channel="#c", header="H")
    digest.add(notion_page_id="a", reason="missing_hook", message="falta Titulo")
    digest.add(notion_page_id="b", reason="missing_caption", message="falta Caption")
    digest.add(notion_page_id=PAGE, reason="programado_sin_metricool", message="sin post")
    digest.add(notion_page_id=PAGE, reason="reel_manual_sin_portada", message="sin portada")
    text = digest.render()
    assert text.count("Cómo arreglar:") == 3
    assert 'llena "Titulo" y "Caption"' in text
    assert "pasar la pieza a Aprobado con hora futura" in text
    assert "elegir portada en Metricool antes de la hora" in text
    assert "editar portada desde la app de Instagram" in text


def test_programado_sin_metricool_uses_the_digest_and_24h_dedupe(tmp_path, caplog):
    row = _spacex()
    with caplog.at_level("INFO"):
        stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [])
    assert stats["scheduled"] == 0
    notion.set_status.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    assert slack.call_count == 1
    text = _texts(slack)[0]
    assert NOTION_URL in text
    assert TITLE in text
    assert "está en Programado" in text
    assert "Cómo arreglar: pasar la pieza a Aprobado con hora futura" in text
    assert "para que el sync la cree" in text
    assert "fixture" not in text
    assert "fixture" not in caplog.text
    assert "No Metricool match" in caplog.text

    _stats, _notion, _metricool, again = _run_schedule(tmp_path, [row], [])
    again.assert_not_called()

    _age_dedupe(tmp_path / "slack.json", seconds=25 * 3600)
    _stats, notion, metricool, reminded = _run_schedule(tmp_path, [row], [])
    assert reminded.call_count == 1
    assert NOTION_URL in _texts(reminded)[0]
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_programado_alert_stays_inside_the_rolling_window(tmp_path):
    inside_back = _row("page-back", "Dentro por atrás", datetime(2026, 10, 12, 0, 30, tzinfo=BOG))
    outside_back = _row("page-old", "Fuera por atrás", datetime(2026, 10, 11, 23, 0, tzinfo=BOG))
    inside_ahead = _row(
        "page-ahead", "Dentro por delante", datetime(2026, 10, 29, 22, 0, tzinfo=BOG)
    )
    outside_ahead = _row(
        "page-far", "Fuera por delante", datetime(2026, 10, 30, 1, 0, tzinfo=BOG)
    )
    _stats, _notion, _metricool, slack = _run_schedule(
        tmp_path, [inside_back, outside_back, inside_ahead, outside_ahead], []
    )
    assert slack.call_count == 1
    text = _texts(slack)[0]
    assert "Dentro por atrás" in text
    assert "Dentro por delante" in text
    assert "Fuera por atrás" not in text
    assert "Fuera por delante" not in text
    assert text.count("Cómo arreglar: pasar la pieza") == 1


def test_manual_reel_without_cover_alerts_once_and_does_not_edit(tmp_path):
    row = _spacex()
    post = _reel()
    stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [post])
    assert stats["scheduled"] == 0
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    assert not (tmp_path / "schedule-guard.sqlite3").exists()
    assert slack.call_count == 1
    text = _texts(slack)[0]
    assert NOTION_URL in text and TITLE in text
    assert str(POST_ID) in text
    assert "19/10" in text and "08:58" in text
    assert IG_LINK in text
    assert "sin portada" in text
    assert "elegir portada en Metricool antes de la hora" in text
    assert "editar portada desde la app de Instagram" in text
    assert "está en Programado y no hay ningún post" not in text

    _stats, _notion, _metricool, again = _run_schedule(tmp_path, [row], [post])
    again.assert_not_called()

    _age_dedupe(tmp_path / "slack.json", seconds=20 * 86400)
    _stats, notion, metricool, later = _run_schedule(tmp_path, [row], [post])
    later.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_reel_with_a_cover_or_a_guard_row_or_a_feed_post_stays_quiet(tmp_path):
    row = _spacex()
    covered = _reel(videoThumbnailUrl="https://static.metricool.com/cover.jpg")
    _stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [covered])
    slack.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()

    framed = _reel(post_id=400060751, videoCoverMilliseconds=1500, public_url="")
    _stats, _notion, metricool, slack = _run_schedule(tmp_path, [row], [framed])
    slack.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()

    feed = _reel(post_id=400060752, ig_type="POST", public_url="")
    _stats, _notion, _metricool, slack = _run_schedule(tmp_path, [row], [feed])
    slack.assert_not_called()

    ScheduleGuard(tmp_path / "schedule-guard.sqlite3").reserve(BLOG, PAGE, SLOT)
    bare = _reel(post_id=400060753, public_url="")
    _stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [bare])
    slack.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_zero_milliseconds_is_not_a_cover(tmp_path):
    row = _spacex()
    post = _reel(videoCoverMilliseconds=0, public_url="")
    _stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [post])
    assert slack.call_count == 1
    assert str(POST_ID) in _texts(slack)[0]
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_dry_run_does_not_send_or_write_dedupe(tmp_path, caplog):
    row = _spacex()
    with caplog.at_level("INFO"):
        stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [], dry=True)
    assert stats["scheduled"] == 0
    slack.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    assert not (tmp_path / "slack.json").exists()
    assert not (tmp_path / "schedule-guard.sqlite3").exists()
    assert WEBHOOK not in caplog.text
    assert "[dry-run] Slack:" in caplog.text

    post = _reel()
    _stats, notion, metricool, slack = _run_schedule(tmp_path, [row], [post], dry=True)
    slack.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    assert not (tmp_path / "slack.json").exists()


def test_confirm_logs_programado_gap_into_the_digest(tmp_path):
    row = _spacex()
    stats, notion, metricool, slack = _run_confirm(tmp_path, [row], [])
    assert stats["published"] == 0
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()
    assert slack.call_count == 1
    text = _texts(slack)[0]
    assert NOTION_URL in text
    assert "Cómo arreglar: pasar la pieza a Aprobado con hora futura" in text

    _stats, notion, metricool, dry = _run_confirm(tmp_path, [row], [], dry=True)
    dry.assert_not_called()
    notion.set_status.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_confirm_manual_reel_alerts_once_without_editing_the_post(tmp_path):
    row = _spacex()
    post = _reel(provider_status="PUBLISHED")
    stats, notion, metricool, slack = _run_confirm(tmp_path, [row], [post])
    assert stats["published"] == 1
    notion.set_status.assert_called_once_with(PAGE, "Publicado", dry_run=False)
    metricool.update_scheduled_post.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    assert slack.call_count == 1
    text = _texts(slack)[0]
    assert str(POST_ID) in text and IG_LINK in text
    assert "sin portada" in text

    _stats, notion, metricool, again = _run_confirm(tmp_path, [row], [post])
    again.assert_not_called()
    metricool.update_scheduled_post.assert_not_called()


def test_programacion_webhook_fills_in_when_url_is_empty(tmp_path):
    settings = _settings(tmp_path, SLACK_WEBHOOK_URL=None, SLACK_WEBHOOK_PROGRAMACION=WEBHOOK)
    assert settings.slack_webhook_url == WEBHOOK
    both = _settings(
        tmp_path,
        SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T000/B000/url",
        SLACK_WEBHOOK_PROGRAMACION=WEBHOOK,
    )
    assert both.slack_webhook_url.endswith("/url")
