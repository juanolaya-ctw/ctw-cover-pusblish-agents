"""Instagram reel covers: Titulo hook, cover agent, and videoThumbnailUrl."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.bridge import publish_task_from_row
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.notion.properties import row_from_page

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
PAGE = "3f299829-d217-81cf-83ee-e66e8ef5139b"


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
        CTW_COVER_AGENT_PATH=str(tmp_path / "cover-agent"),
    )
    data.update(extra)
    return Settings(**data)


def _row(**overrides):
    base = dict(
        page_id=PAGE,
        url="https://notion.so/reel",
        channel="Instagram",
        title="Titulo de la publicación largo",
        cover_text="El hook",
        caption="caption del reel que no es el texto de la portada",
        final_file_url="https://drive.google.com/drive/folders/abc",
        content_type="Reels",
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
        protagonistas="Ana",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp_path, row, *, dry, prepare, upload=None, **settings_kw):
    settings = _settings(tmp_path, **settings_kw)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [row]
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.return_value = {"id": 99}
    metricool.normalize_media_url.side_effect = lambda url: {"url": url}
    upload_mock = upload or MagicMock(return_value="https://litter.catbox.moe/cover.png")
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ) as media,
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            side_effect=prepare,
        ) as prepare_mock,
        patch(
            "metricool_sync_posts.cover.attach.upload_public_url",
            upload_mock,
        ),
    ):
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, media, slack, prepare_mock, upload_mock


def _reasons(slack):
    return [call.kwargs["reason"] for call in slack.call_args_list]


def _ready(**extra):
    body = {
        "ready": True,
        "cover_bytes": b"\x89PNG-cover",
        "source": "drive",
        "message": "ok",
    }
    body.update(extra)
    return body


def test_missing_hook_skips_before_media_and_cover_agent(tmp_path, caplog):
    def prepare(*_args, **_kwargs):
        raise AssertionError("empty Titulo must not call the cover agent")

    with caplog.at_level("INFO"):
        stats, notion, metricool, media, slack, prepare_mock, upload = _run(
            tmp_path, _row(cover_text=""), dry=False, prepare=prepare
        )
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    assert stats["reconciled"] == 0
    media.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    upload.assert_not_called()
    prepare_mock.assert_not_called()
    assert "missing_hook" in _reasons(slack)
    assert "reason=missing_hook" in caplog.text


def test_cover_not_ready_reports_agent_reason(tmp_path, caplog):
    def prepare(*_args, **_kwargs):
        return {
            "ready": False,
            "reason": "missing_frame",
            "message": "no frame in the folder",
            "source": "drive",
        }

    with caplog.at_level("INFO"):
        stats, _notion, metricool, media, slack, _prepare, upload = _run(
            tmp_path, _row(), dry=False, prepare=prepare
        )
    assert stats["scheduled"] == 0
    media.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    upload.assert_not_called()
    assert "missing_frame" in _reasons(slack)
    assert "status=not_ready" in caplog.text
    assert "source=drive" in caplog.text
    assert "reason=missing_frame" in caplog.text


def test_ready_cover_bytes_become_video_thumbnail(tmp_path, caplog):
    def prepare(settings, row, dry_run=False):
        assert dry_run is False
        assert row.cover_text == "El hook"
        assert row.title != row.cover_text
        return _ready()

    with caplog.at_level("INFO"):
        stats, notion, metricool, _media, _slack, _prepare, upload = _run(
            tmp_path, _row(), dry=False, prepare=prepare
        )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["videoThumbnailUrl"] == "https://litter.catbox.moe/cover.png"
    assert body["instagramData"]["type"] == "REEL"
    upload.assert_called_once()
    assert upload.call_args.kwargs["min_hours"] == 72
    assert upload.call_args.kwargs["allow_short"] is False
    notion.set_status.assert_called_once()
    assert "status=ready" in caplog.text
    assert "source=drive" in caplog.text


def test_dropbox_cover_url_used_when_durable_upload_fails(tmp_path, caplog):
    def prepare(*_args, **_kwargs):
        return _ready(
            source="dropbox",
            cover_url="https://www.dropbox.com/s/abc/cover.png?dl=0",
        )

    attempts: list[dict] = []

    def upload(*_args, **kwargs):
        attempts.append(kwargs)
        raise RuntimeError("litterbox down")

    with caplog.at_level("INFO"):
        stats, _notion, metricool, _media, _slack, _prepare, _upload = _run(
            tmp_path, _row(), dry=False, prepare=prepare, upload=upload
        )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert "dl=1" in body["videoThumbnailUrl"]
    assert "dropbox.com" in body["videoThumbnailUrl"]
    assert attempts == [{"min_hours": 72, "allow_short": False}]
    assert "source=dropbox" in caplog.text
    assert "status=ready" in caplog.text


def test_dry_run_skips_agent_without_readonly_mode_and_does_not_upload(tmp_path, caplog):
    def prepare(*_args, **_kwargs):
        return {"ready": False, "dry_run_skipped": True, "reason": "dry_run"}

    with caplog.at_level("INFO"):
        stats, notion, metricool, _media, _slack, prepare_mock, upload = _run(
            tmp_path, _row(), dry=True, prepare=prepare
        )
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    upload.assert_not_called()
    assert prepare_mock.call_args.kwargs["dry_run"] is True
    assert "would prepare cover (titulo=El hook)" in caplog.text
    assert "status=would_prepare" in caplog.text
    assert "source=-" in caplog.text


def test_dry_run_readonly_agent_does_not_upload_bytes(tmp_path, caplog):
    def prepare(*_args, dry_run=False, **_kwargs):
        assert dry_run is True
        return _ready()

    with caplog.at_level("INFO"):
        stats, _notion, metricool, _media, _slack, _prepare, upload = _run(
            tmp_path, _row(), dry=True, prepare=prepare
        )
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_not_called()
    upload.assert_not_called()
    assert "status=ready" in caplog.text
    assert "source=drive" in caplog.text
    assert "Would upload cover" in caplog.text


def test_multi_network_reel_gets_one_thumbnail(tmp_path):
    def prepare(*_args, **_kwargs):
        return _ready(source="dropbox")

    row = _row(channel="Instagram, TikTok", channels=["Instagram", "TikTok"])
    stats, _notion, metricool, _media, _slack, _prepare, _upload = _run(
        tmp_path, row, dry=False, prepare=prepare
    )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert [item["network"] for item in body["providers"]] == ["instagram", "tiktok"]
    assert body["videoThumbnailUrl"] == "https://litter.catbox.moe/cover.png"
    assert body["instagramData"]["type"] == "REEL"


def test_trial_reel_requires_cover(tmp_path):
    def prepare(*_args, **_kwargs):
        return {"ready": False, "reason": "render_failed", "message": "render failed"}

    row = _row(title="Trials: cheat code", content_type="Video")
    stats, _notion, metricool, media, slack, _prepare, _upload = _run(
        tmp_path, row, dry=False, prepare=prepare
    )
    assert stats["scheduled"] == 0
    media.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    assert "render_failed" in _reasons(slack)


def test_static_does_not_need_a_cover_and_youtube_needs_a_hook(tmp_path):
    def prepare(*_args, **_kwargs):
        raise AssertionError("non-reel must not prepare a cover")

    static = _row(content_type="Piezas estática", cover_text="", page_id=PAGE)
    stats, _notion, metricool, media, slack, prepare_mock, upload = _run(
        tmp_path, static, dry=False, prepare=prepare
    )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert "videoThumbnailUrl" not in body
    assert "youtubeData" not in body
    prepare_mock.assert_not_called()
    upload.assert_not_called()
    media.assert_called()
    assert "missing_hook" not in _reasons(slack)

    youtube = _row(
        channel="YouTube",
        content_type="Video",
        title="Trials: Cursos Google",
        cover_text="",
        caption="el caption interno no puede ser el titulo de youtube",
        page_id="3f299829-d217-81cf-83ee-e66e8ef5139c",
    )
    stats, notion, metricool, media, slack, prepare_mock, upload = _run(
        tmp_path, youtube, dry=False, prepare=prepare
    )
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    media.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    prepare_mock.assert_not_called()
    upload.assert_not_called()
    assert "missing_hook" in _reasons(slack)


def test_require_cover_false_schedules_reel_without_thumbnail(tmp_path):
    def prepare(*_args, **_kwargs):
        return {"ready": False, "reason": "unsupported_link", "message": "not a folder"}

    stats, _notion, metricool, _media, slack, _prepare, upload = _run(
        tmp_path,
        _row(),
        dry=False,
        prepare=prepare,
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert "videoThumbnailUrl" not in body
    upload.assert_not_called()
    assert "unsupported_link" not in _reasons(slack)


def test_existing_miniatura_is_the_cover_source(tmp_path, caplog):
    def prepare(*_args, **_kwargs):
        raise AssertionError("a public miniatura does not need a new render")

    row = _row(miniatura_url="https://cdn.example/already.jpg")
    with caplog.at_level("INFO"):
        stats, _notion, metricool, _media, _slack, prepare_mock, upload = _run(
            tmp_path, row, dry=False, prepare=prepare
        )
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["videoThumbnailUrl"] == "https://cdn.example/already.jpg"
    prepare_mock.assert_not_called()
    upload.assert_not_called()
    assert "source=existing" in caplog.text
    assert "status=ready" in caplog.text


def test_row_from_page_keeps_cover_text_apart_from_the_title():
    page = {
        "id": PAGE,
        "url": "https://notion.so/reel",
        "properties": {
            "Titulo de la publicación ": {
                "type": "title",
                "title": [{"plain_text": "Nombre largo de la tarea"}],
            },
            "Titulo": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Hook de portada"}],
            },
            "Canal ": {
                "type": "multi_select",
                "multi_select": [{"name": "Instagram"}],
            },
        },
    }
    names = {
        "status": "Estado de la publicación",
        "publication": "Publicación",
        "channel": "Canal ",
        "caption": "Caption",
        "final_file": "Archivo Final",
        "title": "Titulo de la publicación ",
        "content_type": "Tipo de contenido ",
        "miniatura": "Miniatura",
        "protagonista": "Protagonista",
        "cover_text": "Titulo",
    }
    row = row_from_page(page, names)
    assert row.title == "Nombre largo de la tarea"
    assert row.cover_text == "Hook de portada"
    assert publish_task_from_row(row)["titulo"] == "Hook de portada"
