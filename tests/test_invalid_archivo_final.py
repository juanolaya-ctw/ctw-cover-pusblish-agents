"""Archivo Final must be an http(s) URL before any download."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.media.final_file import invalid_archivo_final_message
from metricool_sync_posts.media.resolve import prepare_media_urls_for_metricool
from metricool_sync_posts.notion.properties import read_link_or_text

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
HINT = (
    '"Archivo Final" no tiene un link. Pega el link de la carpeta o archivo '
    "de Drive o Dropbox (o del video de YouTube)."
)
PAGE = "https://www.notion.so/Pulso-Cap-2-poncho"
LONG_TITLE = ("Pulso Cap. 2 — Poncho de los Ríos — corte final del episodio ") * 4


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
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        SLACK_CHANNEL="#ct-growth",
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    data.update(extra)
    return Settings(**data)


def _row(**overrides):
    base = dict(
        page_id="3e499829-d217-81ce-a105-edddd431fb7d",
        url=PAGE,
        channel="Instagram",
        title="Pulso Cap. 2",
        caption="cinco cosas que amamos de méxico y que el equipo repite en cada pieza",
        final_file_url=LONG_TITLE,
        content_type="Reels",
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
        metricool_id=None,
        metricool_uuid=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp_path, rows, *, dry):
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ) as media,
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
    ):
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, media, slack_post


def test_message_includes_page_link_and_truncated_contents():
    message = invalid_archivo_final_message(PAGE, LONG_TITLE)
    assert HINT in message
    assert PAGE in message
    assert "Contiene: Pulso Cap. 2" in message
    assert LONG_TITLE not in message
    contiene = message.split("Contiene: ", 1)[1]
    assert contiene.endswith("...")
    assert len(contiene) <= 120

    empty = invalid_archivo_final_message(PAGE, "   ")
    assert "Contiene: (vacío)" in empty


def test_reader_keeps_plain_text_from_url_and_rich_text():
    assert (
        read_link_or_text(
            {"Archivo Final": {"type": "url", "url": "  Pulso Cap. 2 — corte  "}},
            "Archivo Final",
        )
        == "Pulso Cap. 2 — corte"
    )
    assert (
        read_link_or_text(
            {
                "Archivo Final": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": "Pulso Cap. 2 — corte"}],
                }
            },
            "Archivo Final",
        )
        == "Pulso Cap. 2 — corte"
    )
    assert (
        read_link_or_text({"Archivo Final": {"type": "url", "url": None}}, "Archivo Final") is None
    )
    assert (
        read_link_or_text(
            {"Archivo Final": {"type": "url", "url": "https://youtu.be/abc123"}},
            "Archivo Final",
        )
        == "https://youtu.be/abc123"
    )


def test_prepare_does_not_request_a_non_http_archivo(tmp_path):
    settings = _settings(tmp_path)

    def _explode(*_args, **_kwargs):
        raise AssertionError("httpx must not be called for a non-url Archivo Final")

    with patch("metricool_sync_posts.media.download.httpx.stream", _explode):
        urls = prepare_media_urls_for_metricool(
            settings=settings,
            metricool=MagicMock(),
            raw_archivo_final="Pulso Cap. 2 — Poncho de los Ríos",
            content_type="Reels",
            dry_run=False,
        )
        schemeless = prepare_media_urls_for_metricool(
            settings=settings,
            metricool=MagicMock(),
            raw_archivo_final="www.dropbox.com/s/abc/file.mp4?dl=0",
            content_type="Reels",
            dry_run=False,
        )
    assert urls == []
    assert schemeless == []


@pytest.mark.parametrize(
    "raw",
    [
        LONG_TITLE,
        "www.dropbox.com/scl/fi/abc/video.mp4?dl=0",
        "drive.google.com/drive/folders/abc",
        "",
        "   ",
        None,
    ],
)
def test_schedule_skips_unusable_archivo_final(tmp_path, raw):
    row = _row(final_file_url=raw)
    stats, notion, metricool, media, slack_post = _run(tmp_path, [row], dry=False)
    assert stats == {"queried": 1, "scheduled": 0, "skipped": 1, "errors": 0, "reconciled": 0}
    media.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    slack_post.assert_called_once()
    payload = slack_post.call_args.kwargs["json"]
    assert payload["channel"] == "#ct-growth"
    assert HINT in payload["text"]
    assert PAGE in payload["text"]
    assert "Contiene:" in payload["text"]
    assert "Request URL is missing" not in payload["text"]
    if raw and str(raw).strip() and len(" ".join(str(raw).split())) <= 120:
        assert " ".join(str(raw).split()) in payload["text"]


def test_invalid_archivo_final_dedupes_same_page_and_reason(tmp_path):
    row = _row()
    first, *_rest, slack_post = _run(tmp_path, [row], dry=False)
    assert first["skipped"] == 1
    assert first["errors"] == 0
    slack_post.assert_called_once()

    _second_stats, *_rest, slack_again = _run(tmp_path, [row], dry=False)
    slack_again.assert_not_called()

    other = _row(page_id="3f099829-d217-818c-925d-cfb5304410e7", url="https://www.notion.so/otra")
    _third, *_rest, slack_other = _run(tmp_path, [other], dry=False)
    slack_other.assert_called_once()
    assert "https://www.notion.so/otra" in slack_other.call_args.kwargs["json"]["text"]


def test_dry_run_invalid_archivo_does_not_consume_dedupe(tmp_path):
    row = _row(final_file_url="Pulso Cap. 2 — corte")
    stats, _notion, _metricool, media, slack_post = _run(tmp_path, [row], dry=True)
    assert stats["skipped"] == 1
    assert stats["errors"] == 0
    media.assert_not_called()
    slack_post.assert_not_called()
    assert not (tmp_path / "slack.json").exists()

    live, *_rest, slack_live = _run(tmp_path, [row], dry=False)
    assert live["errors"] == 0
    slack_live.assert_called_once()
    text = slack_live.call_args.kwargs["json"]["text"]
    assert HINT in text
    assert "Contiene: Pulso Cap. 2 — corte" in text
