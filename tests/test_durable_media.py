"""File types, expiry checks, and uuid identity.

Media bytes are uploaded to Metricool. Host-order tests live in
test_metricool_upload.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.reel import resolve_instagram_reel_cover
from metricool_sync_posts.jobs.confirm_published import run_confirm_published
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.jobs.sync_dates import run_sync_dates
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.expiry import media_url_live
from metricool_sync_posts.media.filetype import ensure_media_suffix, sniff_header
from metricool_sync_posts.metricool.matching import (
    find_duplicate_candidates,
    find_sync_match,
    format_match_evidence,
)

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
LATER = NOW + timedelta(hours=18)
PNG = b"\x89PNG\r\n\x1a\n" + b"IHDR"
JPG = b"\xff\xd8\xff" + b"\x00" * 8
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"body"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 4
PAGE = "3f299829-d217-81cf-83ee-e66e8ef5139b"


def _settings(tmp_path: Path | None = None, **extra) -> Settings:
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID="5822365",
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        TRANSFER_SH=False,
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    if tmp_path is not None:
        data["MEDIA_WORK_DIR"] = tmp_path / "media"
        data["SLACK_DEDUPE_FILE"] = tmp_path / "slack.json"
    data.update(extra)
    return Settings(**data)


def test_magic_bytes_and_drive_mime_never_bin(tmp_path: Path):
    assert sniff_header(PNG).ext == ".png"
    assert sniff_header(JPG).content_type == "image/jpeg"
    assert sniff_header(WEBP).ext == ".webp"
    assert sniff_header(MP4).ext == ".mp4"
    assert sniff_header(MOV).ext == ".mov"
    assert sniff_header(MOV).content_type == "video/quicktime"

    named = tmp_path / "download.bin"
    named.write_bytes(PNG)
    renamed, kind = ensure_media_suffix(named, mime="application/octet-stream")
    assert renamed.suffix == ".png"
    assert kind.ext == ".png"
    assert not renamed.name.endswith(".bin")

    unknown = tmp_path / "blob.bin"
    unknown.write_bytes(b"not-a-media-file")
    fallback, mime_kind = ensure_media_suffix(unknown, mime="image/jpeg")
    assert fallback.suffix == ".jpg"
    assert mime_kind.content_type == "image/jpeg"

    empty = tmp_path / "nope.bin"
    empty.write_bytes(b"????")
    with pytest.raises(MediaHostError, match=r"\.bin"):
        ensure_media_suffix(empty, mime="application/octet-stream")


def test_schedule_skips_and_notifies_when_host_fails(tmp_path: Path):
    row = SimpleNamespace(
        page_id=PAGE,
        url=f"https://www.notion.so/{PAGE}",
        channel="Instagram",
        title="Carrusel del viernes",
        caption="cinco imagenes del carrusel que salen manana en la manana",
        final_file_url="https://drive.google.com/file/d/abc/photo.bin",
        content_type="Carrusel",
        publication=LATER,
        miniatura_url=None,
        status="Aprobado - Edición Final",
        metricool_id=None,
        metricool_uuid=None,
        cover_text="hook",
        channels=["Instagram"],
        protagonistas="",
    )
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [row]
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            side_effect=MediaHostError(
                "media_host_failed: Dropbox app lacks the sharing.write scope"
            ),
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "media_host_failed"
    assert PAGE in slack.call_args.kwargs["message"] or row.url in slack.call_args.kwargs["message"]


def test_cover_host_failure_skips_row(tmp_path: Path):
    settings = _settings(
        tmp_path,
        REQUIRE_COVER_FOR_SCHEDULE=True,
        CTW_COVER_AGENT_PATH="/tmp/agent",
    )
    row = SimpleNamespace(
        page_id=PAGE,
        url=f"https://www.notion.so/{PAGE}",
        channel="Instagram",
        title="Un reel",
        cover_text="El hook",
        caption="caption",
        final_file_url="https://drive.google.com/file/d/abc/view",
        content_type="Reels",
        publication=LATER,
        miniatura_url=None,
        protagonistas="",
    )
    with (
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={"ready": True, "cover_bytes": PNG, "source": "drive"},
        ),
        patch(
            "metricool_sync_posts.cover.attach.cover_png_to_jpeg",
            return_value=b"\xff\xd8\xffcover",
        ),
        patch(
            "metricool_sync_posts.cover.attach.host_cover_jpeg",
            side_effect=MediaHostError("Metricool cover upload failed"),
        ),
    ):
        decision = resolve_instagram_reel_cover(
            settings=settings,
            row=row,
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    assert decision.skip_reason == "media_host_failed"
    assert "Metricool cover upload failed" in (decision.skip_message or "")


def _programado(page_id, caption, when, **extra):
    row = dict(
        page_id=page_id,
        url=f"https://www.notion.so/{page_id}",
        channel="Instagram",
        title="titulo",
        caption=caption,
        publication=when,
        metricool_id=extra.pop("metricool_id", None),
        metricool_uuid=extra.pop("metricool_uuid", None),
    )
    row.update(extra)
    return SimpleNamespace(**row)


def _post(*, post_id, text, when, providers, uuid="uuid-1", media=None, thumb=None):
    body = {
        "id": post_id,
        "uuid": uuid,
        "text": text,
        "publicationDate": {"dateTime": when, "timezone": TZ},
        "providers": providers,
    }
    if media is not None:
        body["media"] = media
    if thumb is not None:
        body["videoThumbnailUrl"] = thumb
    return body


def test_confirm_notifies_media_expired_for_pending_posts(tmp_path: Path):
    text = "caption larga del carrusel que se publica esta tarde en instagram"
    soon = NOW + timedelta(hours=3)
    row = _programado(PAGE, text, soon)
    post = _post(
        post_id=18,
        text=text,
        when="2026-10-09T18:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="carousel-uuid",
        media=["https://litter.catbox.moe/gone.png", "https://litter.catbox.moe/ok.png"],
        thumb="https://uguu.se/cover.png",
    )
    settings = _settings(tmp_path, SLACK_WEBHOOK_URL="https://hooks.slack.test/abc")
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]

    def live(url, **_kwargs):
        if url.endswith("gone.png") or "uguu.se" in url:
            return False, 404
        return True, 200

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
        patch(
            "metricool_sync_posts.jobs.confirm_published.media_url_live",
            side_effect=live,
        ),
        patch("metricool_sync_posts.jobs.confirm_published.notify_slack") as slack,
    ):
        stats = run_confirm_published(settings=settings, dry_run=False)
    assert stats["pending"] == 1
    assert stats["media_expired"] == 1
    assert stats["published"] == 0
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "media_expired"
    assert row.url in slack.call_args.kwargs["message"]
    assert "404" in slack.call_args.kwargs["message"]


def test_confirm_ignores_live_media_and_posts_outside_24h(tmp_path: Path):
    text = "otra caption larga que identifica la pieza de la semana que viene"
    later = NOW + timedelta(hours=30)
    row = _programado("page-later", text, later)
    post = _post(
        post_id=19,
        text=text,
        when="2026-10-10T21:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        media=["https://cdn.example/still.png"],
    )
    settings = _settings(tmp_path)
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
        patch("metricool_sync_posts.jobs.confirm_published.media_url_live") as live,
        patch("metricool_sync_posts.jobs.confirm_published.notify_slack") as slack,
    ):
        stats = run_confirm_published(settings=settings, dry_run=False)
    assert stats["media_expired"] == 0
    live.assert_not_called()
    assert all(call.kwargs["reason"] != "media_expired" for call in slack.call_args_list)


def test_media_url_live_accepts_head_or_get():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/head-ok"):
            return httpx.Response(200 if request.method == "HEAD" else 500)
        if request.url.path.endswith("/get-ok"):
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200, content=PNG)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert media_url_live("https://cdn.example/head-ok", client=client) == (True, 200)
    assert media_url_live("https://cdn.example/get-ok", client=client) == (True, 200)
    ok, status = media_url_live("https://cdn.example/missing", client=client)
    assert ok is False
    assert status == 404


def test_sync_match_prefers_uuid_over_stale_id():
    caption = "texto que no deberia decidir cuando el uuid ya identifica el post"
    stale = _post(
        post_id=100,
        text="unrelated caption that belongs to a different piece entirely",
        when="2026-10-01T08:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="someone-else",
    )
    current = _post(
        post_id=200,
        text="also unrelated after Metricool minted a new id for the same uuid",
        when="2026-10-02T08:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="stable-uuid",
    )
    match = find_sync_match(
        notion_caption=caption,
        notion_title="Titulo",
        notion_channel="Instagram",
        notion_publication=LATER,
        candidates=[stale, current],
        tz_name=TZ,
        metricool_id="100",
        metricool_uuid="stable-uuid",
    )
    assert match is not None
    assert match["id"] == 200
    assert match["uuid"] == "stable-uuid"
    evidence = format_match_evidence(
        current,
        caption=caption,
        title="Titulo",
        tz_name=TZ,
        notion_publication=LATER,
        stored_id="100",
        stored_uuid="stable-uuid",
    )
    assert "reason=uuid" in evidence


def test_duplicate_hits_with_the_same_uuid_are_one_post():
    caption = "cinco cosas que amamos de mexico y que el equipo repite en cada pieza"
    old = _post(
        post_id=100,
        text=caption,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="same-piece",
    )
    new = _post(
        post_id=250,
        text=caption,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="same-piece",
    )
    hits = find_duplicate_candidates(
        [old, new],
        caption=caption,
        title="Carrusel",
        network="instagram",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 11, 15, 0, tzinfo=BOG),
    )
    assert len(hits) == 1
    assert hits[0]["uuid"] == "same-piece"


def test_schedule_claim_follows_uuid_when_id_changed(tmp_path: Path):
    caption = "cinco cosas que amamos de mexico y que el equipo repite en cada pieza"
    approved = SimpleNamespace(
        page_id="3f299829-d217-81cf-83ee-e66e8ef5139b",
        url="https://www.notion.so/approved",
        channel="Instagram",
        title="Otra fila",
        caption=caption,
        final_file_url="https://cdn.example/a.png",
        content_type="Post",
        publication=datetime(2026, 10, 11, 15, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
        metricool_id=None,
        metricool_uuid=None,
        cover_text="",
        channels=["Instagram"],
        protagonistas="",
    )
    linked = _programado(
        "3e699829-d217-80a7-9a14-e93f91cf2070",
        "otro texto que no es el caption de metricool para esta pieza",
        datetime(2026, 10, 11, 12, 0, tzinfo=BOG),
        status="Programado",
        metricool_id="100",
        metricool_uuid="stable-uuid",
        final_file_url="https://cdn.example/old.png",
        content_type="Post",
        cover_text="",
        channels=["Instagram"],
        protagonistas="",
    )
    live = _post(
        post_id=250,
        text=caption,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="stable-uuid",
    )
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [approved]
    notion.fetch_linked_in_window.return_value = [linked]
    metricool.get_scheduled_posts.return_value = [live]
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/a.png"],
        ) as media,
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["reconciled"] == 0
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "slot_conflict"


def test_sync_dates_logs_new_id_and_keeps_uuid(tmp_path: Path, caplog):
    text = "Truora ya hizo algo que muchas startups siguen sin animarse a copiar"
    row = _programado(
        "page-truora",
        text,
        datetime(2026, 10, 11, 12, tzinfo=BOG),
        title="Carrusel: The bridge Truora",
    )
    post = _post(
        post_id=610,
        text=text,
        when="2026-10-11T15:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        uuid="truora-uuid",
    )
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]
    metricool.update_scheduled_post.return_value = {"id": 999, "uuid": "truora-uuid"}
    with (
        patch("metricool_sync_posts.jobs.sync_dates.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.sync_dates.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.sync_dates.now_in", return_value=NOW),
        caplog.at_level("INFO"),
    ):
        stats = run_sync_dates(settings=settings, dry_run=False)
    assert stats["updated"] == 1
    args = metricool.update_scheduled_post.call_args
    assert args.args[0] == "610"
    assert args.kwargs["uuid"] == "truora-uuid"
    assert "999" in caplog.text
    assert "truora-uuid" in caplog.text
    assert "was 610" in caplog.text
