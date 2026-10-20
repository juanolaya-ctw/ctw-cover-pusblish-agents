"""Shared-folder Dropbox downloads, reel stills, and sharing.read errors."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from metricool_sync_posts.cli.dropbox_check import main as dropbox_check_main
from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.reel import resolve_instagram_reel_cover
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.media.dropbox_host import (
    FILE_NOT_FOUND_REASON,
    SHARING_READ_REASON,
    DropboxCheck,
    check_shared_folder,
    cover_match_key,
    download_shared_entry,
    download_shared_file,
    download_shared_link_file,
    find_shared_cover,
    pick_cover_image,
    scrub_secrets,
    sharing_read_text,
)
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.slack.notify import notify_slack

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
LATER = NOW + timedelta(hours=18)
PNG = b"\x89PNG\r\n\x1a\n" + b"IHDR"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
FOLDER = "https://www.dropbox.com/scl/fo/abc/folder?rlkey=secret&dl=1"
FILE_LINK = "https://www.dropbox.com/scl/fi/abc123/clip.mp4?rlkey=secret&dl=0"
STILL = "CT_Oct_7D_P6.00_02_46_00.Still001.png"
VIDEO = "CT_Oct_7D_P6.00_02_46_00.mp4"
PAGE = "3f299829-d217-8025-b213-fe29378a3129"
SCOPE_BODY = {
    "error_summary": "missing_scope/",
    "error": {".tag": "missing_scope", "required_scope": "sharing.read"},
}
NOT_FOUND_BODY = {
    "error_summary": "path/not_found/",
    "error": {".tag": "path", "path": {".tag": "not_found"}},
}


def _settings(tmp_path: Path | None = None, **extra) -> Settings:
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID="5822365",
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        DROPBOX_APP_KEY="app-key",
        DROPBOX_APP_SECRET="app-secret",
        DROPBOX_REFRESH_TOKEN="refresh-token",
    )
    if tmp_path is not None:
        data["MEDIA_WORK_DIR"] = tmp_path / "media"
        data["SLACK_DEDUPE_FILE"] = tmp_path / "slack.json"
    data.update(extra)
    return Settings(**data)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _token_response(*, scope: str | None = None) -> httpx.Response:
    body = {"access_token": "dbx-token"}
    if scope is not None:
        body["scope"] = scope
    return httpx.Response(200, json=body)


class _Metricool:
    def upload_planner_media(self, data, *, content_type, file_extension):
        del data, content_type
        return f"https://static.metricool.com/video/1/202610/{file_extension}"


def test_shared_folder_video_downloads_via_get_shared_link_file(tmp_path: Path):
    name = "reel\u00a0final.mp4"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/sharing/get_shared_link_file"):
            arg_header = request.headers["Dropbox-API-Arg"]
            assert arg_header.isascii()
            arg = json.loads(arg_header)
            assert arg == {"url": FOLDER, "path": f"/{name}"}
            assert "\u00a0" not in arg_header
            return httpx.Response(200, content=MP4)
        if url.endswith("/files/download"):
            raise AssertionError("foreign shared file must not use files/download")
        return httpx.Response(500, text=url)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    dest = tmp_path / "clip.bin"
    download_shared_link_file(
        _settings(),
        FOLDER,
        f"/{name}",
        dest,
        client=real_client(transport=transport),
    )
    assert dest.read_bytes() == MP4
    assert any(url.endswith("/sharing/get_shared_link_file") for url in seen)

    with patch(
        "metricool_sync_posts.media.dropbox_host.httpx.Client",
        side_effect=lambda *args, **kwargs: real_client(transport=transport),
    ):
        uploaded = prepare_media_for_metricool(
            settings=_settings(tmp_path),
            metricool=_Metricool(),  # type: ignore[arg-type]
            source_url=FOLDER,
            dry_run=False,
            dropbox_file_id="id:FOREIGN",
            dropbox_name=name,
            dropbox_path=f"/{name}",
            dropbox_in_account=False,
            networks=[],
        )
    assert uploaded == "https://static.metricool.com/video/1/202610/mp4"


def test_cover_found_in_shared_folder_without_path_lower(tmp_path: Path):
    """The still is listed with shared_link even when path_lower is absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/files/list_folder"):
            body = json.loads(request.content)
            assert body["shared_link"]["url"] == FOLDER
            assert body["path"] == ""
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            ".tag": "file",
                            "name": VIDEO,
                            "id": "id:VID",
                            "size": 90,
                        },
                        {
                            ".tag": "file",
                            "name": STILL,
                            "id": "id:STILL",
                            "size": 12,
                        },
                    ],
                    "has_more": False,
                },
            )
        if url.endswith("/sharing/get_shared_link_file"):
            arg = json.loads(request.headers["Dropbox-API-Arg"])
            assert arg == {"url": FOLDER, "path": f"/{STILL}"}
            return httpx.Response(200, content=PNG)
        if url.endswith("/files/download"):
            raise AssertionError("cover in another account must not use files/download")
        return httpx.Response(500, text=url)

    client = _client(handler)
    image = find_shared_cover(_settings(), FOLDER, client=client)
    assert image is not None
    assert image["name"] == STILL
    assert "path_lower" not in image
    dest = tmp_path / "cover.png"
    download_shared_entry(_settings(), FOLDER, image, dest, client=client)
    assert dest.read_bytes() == PNG


def test_nested_folder_without_path_lower_is_not_the_account_root():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/files/list_folder"):
            body = json.loads(request.content)
            assert "shared_link" in body, "listing without shared_link reads the account root"
            calls.append(body)
            if body["path"] == "":
                return httpx.Response(
                    200,
                    json={
                        "entries": [
                            {".tag": "folder", "name": "Exports", "id": "id:DIR"},
                        ],
                        "has_more": False,
                    },
                )
            if body["path"] == "/Exports":
                return httpx.Response(
                    200,
                    json={
                        "entries": [
                            {".tag": "file", "name": STILL, "id": "id:STILL", "size": 4},
                        ],
                        "has_more": False,
                    },
                )
            raise AssertionError(f"unexpected list path {body['path']!r}")
        return httpx.Response(500, text=url)

    image = find_shared_cover(_settings(), FOLDER, client=_client(handler))
    assert image is not None
    assert image["name"] == STILL
    assert image["relative_path"] == f"/Exports/{STILL}"
    assert [body["path"] for body in calls] == ["", "/Exports"]
    assert all(body["shared_link"]["url"] == FOLDER for body in calls)


def test_missing_scope_sharing_read_message(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/sharing/get_shared_link_file"):
            return httpx.Response(401, json=SCOPE_BODY)
        return httpx.Response(500, text=url)

    with pytest.raises(MediaHostError) as exc_info:
        download_shared_link_file(
            _settings(),
            FOLDER,
            f"/{STILL}",
            tmp_path / "cover.png",
            client=_client(handler),
        )
    err = exc_info.value
    assert err.reason == SHARING_READ_REASON
    assert str(err) == sharing_read_text()
    assert "La app de Dropbox no tiene el permiso sharing.read" in str(err)
    assert "no se pudo leer la carpeta compartida" in str(err)
    assert "Cómo arreglar" in str(err)
    assert "sharing.read" in str(err)
    assert "reautoriza" in str(err)
    assert "link directo" in str(err)
    assert "missing_cover" not in str(err)
    assert "missing_frame" not in str(err)
    assert "portada" not in str(err).lower()
    assert "dbx-token" not in str(err)
    assert "refresh-token" not in str(err)


def test_missing_file_is_not_reported_as_missing_cover(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/sharing/get_shared_link_file"):
            return httpx.Response(409, json=NOT_FOUND_BODY)
        return httpx.Response(500, text=url)

    with pytest.raises(MediaHostError) as exc_info:
        download_shared_link_file(
            _settings(),
            FOLDER,
            f"/{STILL}",
            tmp_path / "cover.png",
            client=_client(handler),
        )
    err = exc_info.value
    assert err.reason == FILE_NOT_FOUND_REASON
    assert f"/{STILL}" in str(err)
    assert "no existe" in str(err)
    assert "missing_cover" not in str(err)
    assert err.reason != "missing_cover"


def test_scl_fi_single_file_still_uses_public_download(tmp_path: Path):
    seen: list[str] = []

    def fake_download(url, dest, settings=None):
        del settings
        seen.append(url)
        dest.write_bytes(MP4)
        return dest

    with (
        patch(
            "metricool_sync_posts.media.pipeline.download_to_path",
            side_effect=fake_download,
        ),
        patch(
            "metricool_sync_posts.media.pipeline.download_shared_link_file",
        ) as shared,
        patch("metricool_sync_posts.media.pipeline.download_shared_file") as by_id,
    ):
        uploaded = prepare_media_for_metricool(
            settings=_settings(tmp_path),
            metricool=_Metricool(),  # type: ignore[arg-type]
            source_url=FILE_LINK,
            dry_run=False,
            networks=[],
        )
    assert uploaded.endswith("/mp4")
    assert seen == [FILE_LINK]
    assert "/scl/fi/" in seen[0]
    shared.assert_not_called()
    by_id.assert_not_called()


def test_own_file_still_downloads_by_id(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/files/download"):
            arg = json.loads(request.headers["Dropbox-API-Arg"])
            assert arg == {"path": "id:OWN"}
            return httpx.Response(200, content=MP4)
        if url.endswith("/sharing/get_shared_link_file"):
            raise AssertionError("a file in our account keeps files/download")
        return httpx.Response(500, text=url)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    with patch(
        "metricool_sync_posts.media.dropbox_host.httpx.Client",
        side_effect=lambda *args, **kwargs: real_client(transport=transport),
    ):
        uploaded = prepare_media_for_metricool(
            settings=_settings(tmp_path),
            metricool=_Metricool(),  # type: ignore[arg-type]
            source_url=FOLDER,
            dry_run=False,
            dropbox_file_id="id:OWN",
            dropbox_name="reel.mp4",
            dropbox_path="/reel.mp4",
            dropbox_in_account=True,
            networks=[],
        )
    assert uploaded.endswith("/mp4")
    dest = tmp_path / "own.mp4"
    download_shared_file(
        _settings(),
        "id:OWN",
        dest,
        client=real_client(transport=transport),
    )
    assert dest.read_bytes() == MP4


def test_non_ascii_names_match_and_download_header_is_ascii(tmp_path: Path):
    nbsp = "reel\u00a0final.mp4"
    curly = "Juan\u2019s clip.mp4"
    straight_still = "Juan's clip.Still001.png"
    assert cover_match_key(nbsp) == cover_match_key("reel final.Still001.png")
    assert cover_match_key(VIDEO) == cover_match_key(STILL)
    assert cover_match_key(curly) == cover_match_key(straight_still)
    picked = pick_cover_image(
        [
            {".tag": "file", "name": curly, "id": "id:VID", "size": 8},
            {".tag": "file", "name": straight_still, "id": "id:STILL", "size": 4},
            {".tag": "file", "name": "notas.png", "id": "id:OTHER", "size": 1},
        ]
    )
    assert picked is not None
    assert picked["name"] == straight_still

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/sharing/get_shared_link_file"):
            header = request.headers["Dropbox-API-Arg"]
            assert header.isascii()
            assert "\\u00a0" in header or "\\u2019" in header
            arg = json.loads(header)
            assert arg["path"] in {f"/{nbsp}", f"/{curly}"}
            return httpx.Response(200, content=MP4)
        return httpx.Response(500, text=url)

    client = _client(handler)
    for name in (nbsp, curly):
        dest = tmp_path / "clip.bin"
        download_shared_link_file(_settings(), FOLDER, f"/{name}", dest, client=client)
        assert dest.read_bytes() == MP4


def test_check_reports_missing_sharing_read_without_secrets():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith("/oauth2/token"):
            assert "refresh-token" in request.content.decode()
            return _token_response(scope="files.metadata.read files.content.read")
        return httpx.Response(500, text="should not be called")

    result = check_shared_folder(_settings(), FOLDER, client=_client(handler))
    assert result.ok is False
    assert result.message == sharing_read_text()
    assert "dbx-token" not in result.message
    assert "refresh-token" not in result.message
    assert "app-secret" not in result.message
    assert "rlkey=secret" not in result.message
    assert not any(url.endswith("/files/list_folder") for url in seen)
    assert not any("get_shared_link_metadata" in url for url in seen)


def test_check_lists_shared_folder_when_scope_is_present():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            return _token_response(
                scope="files.metadata.read files.content.read sharing.read"
            )
        if url.endswith("/sharing/get_shared_link_metadata"):
            body = json.loads(request.content)
            assert body["url"] == FOLDER
            return httpx.Response(200, json={".tag": "folder", "name": "Reels"})
        if url.endswith("/files/list_folder"):
            body = json.loads(request.content)
            assert body["shared_link"]["url"] == FOLDER
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {".tag": "file", "name": VIDEO, "id": "id:VID", "size": 3},
                        {".tag": "file", "name": STILL, "id": "id:STILL", "size": 2},
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(500, text=url)

    result = check_shared_folder(_settings(), FOLDER, client=_client(handler))
    assert result.ok is True
    assert result.file_count == 2
    assert STILL in result.names
    assert "sharing.read" in result.message
    assert "dbx-token" not in result.message
    assert "refresh-token" not in result.message
    assert "rlkey" not in result.message


def test_check_scrubs_a_token_echoed_by_dropbox():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/oauth2/token"):
            return httpx.Response(
                400,
                text='invalid_grant refresh_token=refresh-token client_secret=app-secret',
            )
        return httpx.Response(500, text="nope")

    result = check_shared_folder(_settings(), FOLDER, client=_client(handler))
    assert result.ok is False
    assert "refresh-token" not in result.message
    assert "app-secret" not in result.message
    assert "refresh_token=…" in result.message
    assert scrub_secrets("rlkey=secret") == "rlkey=…"


def test_cli_dropbox_check_prints_names_not_the_link(capsys, monkeypatch):
    monkeypatch.setattr(
        "metricool_sync_posts.cli.dropbox_check.load_settings",
        lambda: _settings(),
    )

    def fake(_settings_obj, url, client=None):
        assert "rlkey=secret" in url
        return DropboxCheck(
            ok=True,
            message="OK: el token tiene sharing.read y la carpeta compartida tiene 1 archivo.",
            file_count=1,
            names=(STILL,),
        )

    monkeypatch.setattr(
        "metricool_sync_posts.cli.dropbox_check.check_shared_folder",
        fake,
    )
    code = dropbox_check_main([FOLDER])
    out = capsys.readouterr().out
    assert code == 0
    assert "rlkey=secret" not in out
    assert "refresh-token" not in out
    assert "dbx-token" not in out
    assert STILL in out
    assert "sharing.read" in out


def test_module_entrypoint_dropbox_check(capsys, monkeypatch):
    monkeypatch.setattr(
        "metricool_sync_posts.cli.dropbox_check.load_settings",
        lambda: _settings(),
    )
    monkeypatch.setattr(
        "metricool_sync_posts.cli.dropbox_check.check_shared_folder",
        lambda *_args, **_kwargs: DropboxCheck(False, sharing_read_text()),
    )
    from metricool_sync_posts.__main__ import main as module_main

    code = module_main(["dropbox-check", FOLDER])
    out = capsys.readouterr().out
    assert code == 1
    assert "sharing.read" in out
    assert "Cómo arreglar" in out
    assert "rlkey=secret" not in out
    assert "refresh-token" not in out


def _reel_row(**overrides):
    base = dict(
        page_id=PAGE,
        url=f"https://www.notion.so/{PAGE}",
        channel="Instagram",
        title="Titulo de la publicación",
        cover_text="El hook",
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


def test_reel_cover_scope_error_is_not_missing_cover(tmp_path: Path):
    still = {".tag": "file", "name": STILL, "id": "id:STILL", "relative_path": f"/{STILL}"}

    def boom(*_args, **_kwargs):
        raise MediaHostError(sharing_read_text(), reason=SHARING_READ_REASON)

    with (
        patch(
            "metricool_sync_posts.cover.reel.find_shared_cover",
            return_value=still,
        ),
        patch(
            "metricool_sync_posts.cover.reel.download_shared_entry",
            side_effect=boom,
        ),
        patch(
            "metricool_sync_posts.cover.reel.prepare_cover_for_publish_task",
            return_value={"ready": False, "reason": "missing_frame", "source": "dropbox"},
        ),
    ):
        decision = resolve_instagram_reel_cover(
            settings=_settings(tmp_path, CTW_COVER_AGENT_PATH=str(tmp_path / "agent")),
            row=_reel_row(),
            networks=["instagram"],
            metricool=MagicMock(),
            dry_run=False,
        )
    assert decision.skip_reason == SHARING_READ_REASON
    assert decision.skip_reason != "missing_cover"
    assert decision.skip_reason != "missing_frame"
    assert "Cómo arreglar" in (decision.skip_message or "")


def test_schedule_scope_error_slack_message_and_dry_run_sends_nothing(tmp_path: Path):
    still = {".tag": "file", "name": STILL, "id": "id:STILL", "relative_path": f"/{STILL}"}
    settings = _settings(
        tmp_path,
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        REQUIRE_COVER_FOR_SCHEDULE=True,
    )
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [_reel_row()]
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []

    def boom(*_args, **_kwargs):
        raise MediaHostError(sharing_read_text(), reason=SHARING_READ_REASON)

    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch("metricool_sync_posts.cover.reel.find_shared_cover", return_value=still),
        patch("metricool_sync_posts.cover.reel.download_shared_entry", side_effect=boom),
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
        patch("metricool_sync_posts.slack.digest.post_to_slack") as digest_post,
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    slack_post.assert_not_called()
    digest_post.assert_called_once()
    text = digest_post.call_args.kwargs["text"]
    assert "La app de Dropbox no tiene el permiso sharing.read" in text
    assert "Cómo arreglar" in text
    assert "missing_cover" not in text
    assert "missing_frame" not in text
    assert "refresh-token" not in text
    assert "dbx-token" not in text
    assert "rlkey=secret" not in text

    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch("metricool_sync_posts.cover.reel.find_shared_cover") as find_cover,
        patch("metricool_sync_posts.slack.notify.httpx.post") as slack_post,
        patch("metricool_sync_posts.slack.digest.post_to_slack") as digest_post,
    ):
        dry_stats = run_schedule(settings=settings, dry_run=True)
    assert dry_stats["scheduled"] == 1
    assert dry_stats["skipped"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()
    slack_post.assert_not_called()
    digest_post.assert_not_called()
    find_cover.assert_not_called()
    with patch("metricool_sync_posts.slack.notify.httpx.post") as direct:
        notify_slack(
            webhook_url=settings.slack_webhook_url,
            channel=settings.slack_channel,
            dedupe=MagicMock(),
            notion_page_id=PAGE,
            reason=SHARING_READ_REASON,
            message=sharing_read_text(),
            dry_run=True,
        )
    direct.assert_not_called()
