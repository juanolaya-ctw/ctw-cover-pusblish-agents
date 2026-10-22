"""Dropbox reel stills are designed covers, never a raw frame."""

from __future__ import annotations

import struct
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.reel import resolve_instagram_reel_cover
from metricool_sync_posts.cover.render import COVER_HEIGHT, COVER_WIDTH, png_size, render_hook_cover
from metricool_sync_posts.jobs.schedule import _cover_alert_text, run_schedule
from metricool_sync_posts.media.ffmpeg_util import resolve_ffmpeg

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 21, 8, 13, tzinfo=BOG)
LATER = NOW + timedelta(hours=6)
PAGE = "3f299829-d217-8002-b3d0-eed326299736"
FOLDER = "https://www.dropbox.com/scl/fo/abc/folder?rlkey=secret&dl=0"
STILL_NAME = "CT_Oct_7D_P7.00_00_21_21.Still001.png"
HOOK = "7 días para pensar como founder día 7"
STATIC = "https://static.metricool.com/video/1/202610/cover.jpg"
JPEG = b"\xff\xd8\xffcover"


def _settings(tmp_path: Path, **extra) -> Settings:
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
        DROPBOX_APP_KEY="app-key",
        DROPBOX_APP_SECRET="app-secret",
        DROPBOX_REFRESH_TOKEN="refresh-token",
    )
    data.update(extra)
    return Settings(**data)


def _row(**overrides):
    base = dict(
        page_id=PAGE,
        url=f"https://www.notion.so/{PAGE}",
        channel="Instagram",
        title="7 días para pensar como founder día 7",
        cover_text=HOOK,
        caption="caption del reel que no es el texto de la portada",
        final_file_url=FOLDER,
        content_type="Reels",
        publication=LATER,
        miniatura_url=None,
        status="Aprobado - Edición Final",
        protagonistas="",
        metricool_id=None,
        metricool_uuid=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _still() -> dict:
    return {
        ".tag": "file",
        "name": STILL_NAME,
        "id": "id:STILL",
        "relative_path": f"/{STILL_NAME}",
    }


def _require_ffmpeg():
    if resolve_ffmpeg("ffmpeg") is None:
        pytest.skip("ffmpeg is required to render the hook")


def _portrait_still(path: Path) -> None:
    """9:16 frame with a red band in the top 80px that a center crop must drop."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x2244aa:s=1080x1920",
            "-vf",
            "drawbox=x=0:y=0:w=iw:h=80:color=red:t=fill",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(path),
        ]
    )


def _write_still(src: Path):
    def download(_settings, _archivo, _entry, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest

    return download


def test_unready_agent_renders_hook_over_dropbox_still(tmp_path, caplog):
    _require_ffmpeg()
    portrait = tmp_path / "portrait.png"
    _portrait_still(portrait)
    still_bytes = portrait.read_bytes()
    uploaded: dict = {}

    def png_to_jpeg(_settings, png_path):
        data = Path(png_path).read_bytes()
        uploaded["png"] = data
        return JPEG

    def host(_metricool, jpeg):
        uploaded["jpeg"] = jpeg
        return STATIC

    with (
        caplog.at_level("INFO"),
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={
                "ready": False,
                "reason": "missing_frame",
                "message": "shared folder not readable by the cover agent",
                "source": "dropbox",
            },
        ),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=_still()),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=_write_still(portrait),
        ),
        patch("metricool_sync_posts.cover.attach.cover_png_to_jpeg", side_effect=png_to_jpeg),
        patch("metricool_sync_posts.cover.attach.host_cover_jpeg", side_effect=host),
    ):
        decision = resolve_instagram_reel_cover(
            settings=_settings(tmp_path),
            row=_row(),
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    assert decision.skip_reason is None
    assert decision.cover_url == STATIC
    assert uploaded["jpeg"] == JPEG
    assert uploaded["png"] != still_bytes
    assert png_size(uploaded["png"]) == (COVER_WIDTH, COVER_HEIGHT)
    assert "status=not_ready" in caplog.text
    assert "source=dropbox" in caplog.text
    assert "reason=missing_frame" in caplog.text
    assert "prepare_cover_for_publish_task not ready" in caplog.text
    assert "shared folder not readable" in caplog.text
    assert "status=ready" in caplog.text
    # The red band lived in the cropped-off top of the 9:16 still.
    top = _rgb(uploaded["png"])[0]
    assert top[0] < 200


def test_agent_renderer_gets_the_still_and_raw_frame_is_not_uploaded(tmp_path, caplog):
    still_bytes = b"\x89PNG\r\n\x1a\nRAW-STILL"
    designed = b"\x89PNG\r\n\x1a\nDESIGNED-COVER"
    seen: dict = {}

    def render_cover_on_local_frame(
        settings, *, titulo, frame_bytes, frame_path, dry_run=False, task=None
    ):
        del settings, frame_path, dry_run, task
        seen["titulo"] = titulo
        seen["frame"] = frame_bytes
        return {"ready": True, "cover_bytes": designed, "source": "dropbox"}

    def png_to_jpeg(_settings, png_path):
        seen["uploaded"] = Path(png_path).read_bytes()
        return JPEG

    with (
        caplog.at_level("INFO"),
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={"ready": False, "reason": "unsupported_link", "source": "dropbox"},
        ),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=_still()),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=_write_bytes(still_bytes),
        ),
        patch(
            "metricool_sync_posts.cover.reel.render_cover_on_local_frame",
            side_effect=render_cover_on_local_frame,
        ),
        patch("metricool_sync_posts.cover.attach.cover_png_to_jpeg", side_effect=png_to_jpeg),
        patch(
            "metricool_sync_posts.cover.attach.host_cover_jpeg",
            return_value=STATIC,
        ),
    ):
        decision = resolve_instagram_reel_cover(
            settings=_settings(tmp_path),
            row=_row(),
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    assert decision.cover_url == STATIC
    assert seen["titulo"] == HOOK
    assert seen["frame"] == still_bytes
    assert seen["uploaded"] == designed
    assert seen["uploaded"] != still_bytes
    assert "reason=unsupported_link" in caplog.text
    assert "prepare_cover_for_publish_task not ready" in caplog.text


def test_render_failure_skips_in_spanish_and_does_not_upload(tmp_path, caplog):
    still_bytes = b"\x89PNG\r\n\x1a\nRAW-STILL"

    def render_cover_on_local_frame(*_args, **_kwargs):
        return {
            "ready": False,
            "reason": "render_failed",
            "message": "ffmpeg is not available to render the reel cover",
            "source": "dropbox",
        }

    with (
        caplog.at_level("INFO"),
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value=None,
        ),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=_still()),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=_write_bytes(still_bytes),
        ),
        patch(
            "metricool_sync_posts.cover.reel.render_cover_on_local_frame",
            side_effect=render_cover_on_local_frame,
        ),
        patch("metricool_sync_posts.cover.attach.cover_png_to_jpeg") as png_to_jpeg,
        patch("metricool_sync_posts.cover.attach.host_cover_jpeg") as host,
    ):
        decision = resolve_instagram_reel_cover(
            settings=_settings(tmp_path),
            row=_row(),
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    assert decision.cover_url is None
    assert decision.skip_reason == "render_failed"
    assert "Titulo" in (decision.skip_message or "")
    assert "no se publica el frame sin el hook" in (decision.skip_message or "")
    assert "ffmpeg is not available" in (decision.skip_message or "")
    png_to_jpeg.assert_not_called()
    host.assert_not_called()
    alert = _cover_alert_text(decision)
    assert "portada" in alert
    assert "Titulo" in alert
    assert "reason=no_result" in caplog.text
    assert "prepare_cover_for_publish_task not ready" in caplog.text
    assert "status=not_ready" in caplog.text


def test_unchanged_still_is_not_uploaded(tmp_path):
    still_bytes = b"\x89PNG\r\n\x1a\nRAW-STILL"

    def render_cover_on_local_frame(*_args, **kwargs):
        return {"ready": True, "cover_bytes": kwargs["frame_bytes"], "source": "dropbox"}

    with (
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={"ready": False, "reason": "missing_frame", "source": "dropbox"},
        ),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=_still()),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=_write_bytes(still_bytes),
        ),
        patch(
            "metricool_sync_posts.cover.reel.render_cover_on_local_frame",
            side_effect=render_cover_on_local_frame,
        ),
        patch("metricool_sync_posts.cover.attach.host_cover_jpeg") as host,
    ):
        decision = resolve_instagram_reel_cover(
            settings=_settings(tmp_path),
            row=_row(),
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    host.assert_not_called()
    assert decision.skip_reason == "render_failed"
    assert "sin el Titulo" in (decision.skip_message or "")


def test_render_failure_slack_digest_matches_cover_alerts(tmp_path):
    settings = _settings(
        tmp_path,
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        REQUIRE_COVER_FOR_SCHEDULE=True,
    )
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [_row()]
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []

    def render_cover_on_local_frame(*_args, **_kwargs):
        return {
            "ready": False,
            "reason": "render_failed",
            "message": "sin fuente",
            "source": "dropbox",
        }

    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={
                "ready": False,
                "reason": "missing_frame",
                "message": "no pudo leer la carpeta compartida",
                "source": "dropbox",
            },
        ),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=_still()),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=_write_bytes(b"raw-still"),
        ),
        patch(
            "metricool_sync_posts.cover.reel.render_cover_on_local_frame",
            side_effect=render_cover_on_local_frame,
        ),
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
        patch("metricool_sync_posts.slack.digest.post_to_slack") as digest_post,
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    slack_post.assert_not_called()
    digest_post.assert_called_once()
    text = digest_post.call_args.kwargs["text"]
    assert "portada del Reel" in text
    assert "Titulo" in text
    assert "no se publica el frame sin el hook" in text
    assert "rlkey=secret" not in text
    assert "refresh-token" not in text


def test_hook_render_is_4_5_and_drops_the_9_16_edges(tmp_path):
    _require_ffmpeg()
    portrait = tmp_path / "portrait.png"
    _portrait_still(portrait)
    dest = tmp_path / "cover-designed.png"
    png = render_hook_cover(
        ffmpeg_bin="ffmpeg",
        titulo=HOOK,
        frame_path=portrait,
        dest=dest,
    )
    assert png_size(png) == (COVER_WIDTH, COVER_HEIGHT)
    assert png != portrait.read_bytes()
    pixels = _rgb(png)
    assert pixels[0][0] < 200
    lower = int(COVER_HEIGHT * 0.7) * COVER_WIDTH
    white = sum(1 for r, g, b in pixels[lower:] if r > 230 and g > 230 and b > 230)
    assert white > 500


def _write_bytes(payload: bytes):
    def download(_settings, _archivo, _entry, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    return download


def _rgb(png: bytes) -> list[tuple[int, int, int]]:
    """Decode a PNG to the first row plus a flat list via ffmpeg."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        src = Path(folder) / "in.png"
        raw_path = Path(folder) / "raw.rgb"
        src.write_bytes(png)
        width, height = struct.unpack(">II", png[16:24])
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                str(raw_path),
            ]
        )
        raw = raw_path.read_bytes()
    assert len(raw) == width * height * 3
    pixels = [
        (raw[i], raw[i + 1], raw[i + 2])
        for i in range(0, len(raw), 3)
    ]
    return pixels
