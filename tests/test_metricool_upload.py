"""Metricool planner upload, Dropbox folder files, and Instagram re-encode."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.dropbox_host import (
    download_shared_file,
    list_shared_folder,
    pick_shared_entries,
)
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.ffmpeg_util import (
    IG_MAX_BYTES,
    IG_MAX_VIDEO_BITRATE,
    instagram_reencode_command,
    instagram_reencode_reason,
)
from metricool_sync_posts.media.pipeline import prepare_media_for_metricool
from metricool_sync_posts.media.resolve import prepare_media_urls_for_metricool
from metricool_sync_posts.metricool.media_upload import (
    SIMPLE_MAX,
    plan_ranges,
    sha256_b64,
    upload_planner_bytes,
)

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
LATER = NOW + timedelta(hours=18)
PNG = b"\x89PNG\r\n\x1a\n" + b"IHDR"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8
STATIC = "https://static.metricool.com/video/1/202610/abc123.mp4"
FOLDER = "https://www.dropbox.com/scl/fo/abc/folder?rlkey=secret&dl=1"


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


def test_plan_ranges_simple_under_5mb_and_multipart_chunks():
    assert plan_ranges(SIMPLE_MAX) == [(0, SIMPLE_MAX)]
    assert plan_ranges(20, part_size=8, simple_max=7) == [(0, 8), (8, 16), (16, 20)]
    with pytest.raises(MediaHostError):
        plan_ranges(0)


def test_simple_upload_puts_checksum_and_uses_converted_url():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/v2/media/s3/upload-transactions") and request.method == "PUT":
            body = json.loads(request.content)
            assert body["resourceType"] == "planner"
            assert body["contentType"] == "image/png"
            assert body["fileExtension"] == "png"
            assert body["parts"][0]["hash"] == sha256_b64(PNG)
            assert body["parts"][0]["endByte"] == len(PNG)
            assert request.url.params["userId"] == "1"
            assert request.url.params["blogId"] == "5822365"
            assert request.headers["x-mc-auth"] == "token"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "uploadType": "SIMPLE",
                        "key": "temp/png",
                        "bucket": "metricool-temp",
                        "expiresAt": "2026-10-10T00:00:00Z",
                        "fileUrl": "https://metricool-temp.s3.amazonaws.com/temp/png",
                        "presignedUrl": "https://uploads.example/png?X-Amz-Signature=abc",
                    }
                },
            )
        if request.method == "PUT" and "uploads.example" in str(request.url):
            assert request.headers.get("x-mc-auth") is None
            assert request.headers["content-type"] == "image/png"
            assert request.headers["x-amz-checksum-sha256"] == sha256_b64(PNG)
            assert request.content == PNG
            return httpx.Response(200)
        if request.method == "PATCH":
            body = json.loads(request.content)
            assert body == {"simple": {"fileUrl": "https://metricool-temp.s3.amazonaws.com/temp/png"}}
            return httpx.Response(
                200,
                json={
                    "data": {
                        "bucket": "metricool-temp",
                        "key": "temp/png",
                        "fileUrl": "https://metricool-temp.s3.amazonaws.com/temp/png",
                        "convertedFileUrl": "https://static.metricool.com/video/1/202610/png.png",
                    }
                },
            )
        return httpx.Response(500, text=f"{request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    api = httpx.Client(
        transport=transport,
        base_url="https://app.metricool.com/api",
        headers={"X-Mc-Auth": "token"},
    )
    s3 = httpx.Client(transport=transport)
    url = upload_planner_bytes(
        api,
        PNG,
        params={"userId": "1", "blogId": "5822365"},
        content_type="image/png",
        file_extension="png",
        s3=s3,
    )
    assert url == "https://static.metricool.com/video/1/202610/png.png"
    assert [request.method for request in seen] == ["PUT", "PUT", "PATCH"]


def test_multipart_upload_sends_etags_and_part_checksums():
    payload = b"abcdefghij"  # 10 bytes, 4-byte parts -> 3 parts
    etags = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload-transactions") and request.method == "PUT":
            body = json.loads(request.content)
            assert len(body["parts"]) == 3
            parts = []
            ranges = plan_ranges(len(payload), part_size=4, simple_max=3)
            for index, (start, end) in enumerate(ranges, start=1):
                parts.append(
                    {
                        "partNumber": index,
                        "presignedUrl": f"https://uploads.example/part{index}",
                        "partSize": end - start,
                        "startByte": start,
                        "endByte": end,
                    }
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "uploadType": "MULTIPART",
                        "uploadId": "upload-1",
                        "key": "temp/video.mp4",
                        "bucket": "metricool-temp",
                        "expiresAt": "2026-10-10T00:00:00Z",
                        "parts": parts,
                    }
                },
            )
        if "uploads.example" in str(request.url):
            assert "content-type" not in {key.lower() for key in request.headers}
            assert request.headers["x-amz-checksum-sha256"] == sha256_b64(request.content)
            etag = f'"etag-{request.url.path[-1]}"'
            etags.append(etag)
            return httpx.Response(200, headers={"ETag": etag})
        if request.method == "PATCH":
            body = json.loads(request.content)
            assert body["multipart"]["uploadId"] == "upload-1"
            assert body["multipart"]["key"] == "temp/video.mp4"
            assert body["multipart"]["parts"] == [
                {"partNumber": 1, "etag": etags[0]},
                {"partNumber": 2, "etag": etags[1]},
                {"partNumber": 3, "etag": etags[2]},
            ]
            return httpx.Response(
                200,
                json={
                    "data": {
                        "bucket": "metricool-temp",
                        "key": "temp/video.mp4",
                        "fileUrl": "https://metricool-temp.s3.amazonaws.com/temp/video.mp4",
                        "convertedFileUrl": STATIC,
                    }
                },
            )
        return httpx.Response(500, text=str(request.url))

    transport = httpx.MockTransport(handler)
    api = httpx.Client(transport=transport, base_url="https://app.metricool.com/api")
    url = upload_planner_bytes(
        api,
        payload,
        params={"userId": "1", "blogId": "5822365"},
        content_type="video/mp4",
        file_extension=".mp4",
        s3=httpx.Client(transport=transport),
        part_size=4,
        simple_max=3,
    )
    assert url == STATIC


def test_presigned_put_rejects_metricool_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/upload-transactions"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "uploadType": "SIMPLE",
                        "presignedUrl": "https://uploads.example/x",
                        "fileUrl": "https://metricool-temp.s3.amazonaws.com/x",
                        "key": "x",
                        "bucket": "metricool-temp",
                        "expiresAt": "2026-10-10T00:00:00Z",
                    }
                },
            )
        return httpx.Response(200)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://app.metricool.com/api",
        headers={"X-Mc-Auth": "secret"},
    )
    with pytest.raises(MediaHostError, match="X-Mc-Auth"):
        upload_planner_bytes(
            client,
            PNG,
            params={"userId": "1", "blogId": "5822365"},
            content_type="image/png",
            file_extension="png",
            s3=client,
        )


def test_complete_failure_is_media_host_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/upload-transactions"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "uploadType": "SIMPLE",
                        "presignedUrl": "https://uploads.example/x",
                        "fileUrl": "https://metricool-temp.s3.amazonaws.com/x",
                        "key": "x",
                        "bucket": "metricool-temp",
                        "expiresAt": "2026-10-10T00:00:00Z",
                    }
                },
            )
        if request.method == "PATCH":
            return httpx.Response(400, text="parts missing")
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with pytest.raises(MediaHostError, match="400"):
        upload_planner_bytes(
            httpx.Client(transport=transport, base_url="https://app.metricool.com/api"),
            PNG,
            params={"userId": "1", "blogId": "5822365"},
            content_type="image/png",
            file_extension="png",
            s3=httpx.Client(transport=transport),
        )


def test_instagram_reencode_keeps_fps_and_resolution():
    assert instagram_reencode_reason(size=10, bitrate=1_000_000, width=1080, max_width=1920) is None
    assert (
        instagram_reencode_reason(
            size=10, bitrate=IG_MAX_VIDEO_BITRATE + 1, width=1080, max_width=1920
        )
        == "bitrate"
    )
    assert (
        instagram_reencode_reason(size=IG_MAX_BYTES + 1, bitrate=1, width=1080, max_width=1920)
        == "size"
    )
    assert instagram_reencode_reason(size=10, bitrate=1, width=3840, max_width=1920) == "width"
    cmd = instagram_reencode_command("ffmpeg", Path("in.mp4"), Path("out.mp4"), scale_width=None)
    assert "-r" not in cmd
    assert "14M" in cmd
    assert "-vf" not in cmd
    wide = instagram_reencode_command("ffmpeg", Path("in.mp4"), Path("out.mp4"), scale_width=1920)
    assert "-vf" in wide
    assert "-r" not in wide


def test_pipeline_uploads_bytes_and_reencodes_only_for_instagram(tmp_path: Path):
    settings = _settings(tmp_path)

    def fake_download(url, dest, settings=None):
        del url, settings
        dest.write_bytes(MP4)
        return dest

    metricool = type("M", (), {})()
    calls: list[dict] = []

    def upload(data, *, content_type, file_extension):
        calls.append({"data": data, "content_type": content_type, "file_extension": file_extension})
        return STATIC

    metricool.upload_planner_media = upload  # type: ignore[attr-defined]

    with (
        patch("metricool_sync_posts.media.pipeline.download_to_path", side_effect=fake_download),
        patch(
            "metricool_sync_posts.media.pipeline.apply_instagram_video_limits",
            side_effect=lambda _s, path, **_k: path,
        ) as apply,
    ):
        ig = prepare_media_for_metricool(
            settings=settings,
            metricool=metricool,  # type: ignore[arg-type]
            source_url="https://cdn.example/clip.mp4",
            dry_run=False,
            networks=["instagram", "tiktok"],
            publication=LATER,
        )
        tiktok = prepare_media_for_metricool(
            settings=settings,
            metricool=metricool,  # type: ignore[arg-type]
            source_url="https://cdn.example/clip.mp4",
            dry_run=False,
            networks=["tiktok"],
        )
    assert ig == STATIC
    assert tiktok == STATIC
    assert apply.call_count == 1
    assert calls[0]["content_type"] == "video/mp4"
    assert calls[0]["file_extension"] == "mp4"
    assert calls[0]["data"].startswith(b"\x00\x00\x00\x18ftyp")
    assert "litterbox" not in ig


def test_zip_and_unresolved_folder_are_rejected(tmp_path: Path):
    settings = _settings(tmp_path)

    def fake_download(url, dest, settings=None):
        del url, settings
        dest.write_bytes(b"PK\x03\x04" + b"zip")
        return dest

    with (
        patch("metricool_sync_posts.media.pipeline.download_to_path", side_effect=fake_download),
        pytest.raises(MediaHostError, match="ZIP"),
    ):
        prepare_media_for_metricool(
            settings=settings,
            metricool=None,  # type: ignore[arg-type]
            source_url="https://www.dropbox.com/scl/fi/abc/clip.mp4?dl=1",
            dry_run=False,
        )
    with pytest.raises(MediaHostError, match="folder"):
        prepare_media_for_metricool(
            settings=settings,
            metricool=None,  # type: ignore[arg-type]
            source_url=FOLDER,
            dry_run=False,
        )


def test_dropbox_list_folder_picks_largest_video():
    name = "reel\u00a0final.mp4"
    file_id = "id:VID123"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "get_temporary_link" not in url
        assert "create_shared_link" not in url
        if url.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "dbx-token"})
        if url.endswith("/files/list_folder"):
            body = json.loads(request.content)
            assert body["path"] == ""
            assert body["shared_link"]["url"] == FOLDER
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {".tag": "file", "name": "slide.png", "id": "id:IMG", "size": 10},
                        {".tag": "file", "name": name, "id": file_id, "size": 90},
                        {".tag": "file", "name": "short.mp4", "id": "id:SMALL", "size": 5},
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(500, text=url)

    entries = list_shared_folder(
        _settings(),
        FOLDER,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    picked = pick_shared_entries(entries, carousel=False, stories=False)
    assert picked[0]["id"] == file_id
    assert picked[0]["name"] == name


def test_dropbox_download_by_id_ignores_nbsp_filename(tmp_path: Path):
    name = "reel\u00a0final.mp4"
    file_id = "id:VID123"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/token"):
            body = request.content.decode()
            assert "refresh_token=refresh-token" in body
            return httpx.Response(200, json={"access_token": "dbx-token"})
        if url.endswith("/files/download"):
            arg = json.loads(request.headers["Dropbox-API-Arg"])
            assert arg == {"path": file_id}
            assert "\u00a0" not in request.headers["Dropbox-API-Arg"]
            assert name not in request.headers["Dropbox-API-Arg"]
            return httpx.Response(200, content=MP4)
        return httpx.Response(500, text=url)

    dest = tmp_path / "clip.bin"
    download_shared_file(
        _settings(),
        file_id,
        dest,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert dest.read_bytes() == MP4
    with pytest.raises(MediaHostError, match="file id"):
        download_shared_file(_settings(), f"/{name}", dest)


def test_carousel_folder_uploads_images_and_reel_uploads_video(tmp_path: Path):
    settings = _settings(tmp_path)
    entries = [
        {".tag": "file", "name": "01.png", "id": "id:A", "size": 10},
        {".tag": "file", "name": "02.png", "id": "id:B", "size": 11},
        {".tag": "file", "name": "reel\u00a0final.mp4", "id": "id:VID", "size": 99},
    ]
    downloaded: list[str] = []

    def fake_list(_settings, _url, client=None):
        del client
        return entries

    def fake_download(_settings, file_id, dest, client=None):
        del client
        downloaded.append(file_id)
        dest.write_bytes(PNG if file_id != "id:VID" else MP4)
        return dest

    class Metricool:
        def __init__(self) -> None:
            self.n = 0

        def upload_planner_media(self, data, *, content_type, file_extension):
            del data, content_type
            self.n += 1
            return f"https://static.metricool.com/video/1/202610/{file_extension}-{self.n}"

    with (
        patch("metricool_sync_posts.media.resolve.list_shared_folder", side_effect=fake_list),
        patch(
            "metricool_sync_posts.media.pipeline.download_shared_file",
            side_effect=fake_download,
        ),
    ):
        reel = prepare_media_urls_for_metricool(
            settings=settings,
            metricool=Metricool(),  # type: ignore[arg-type]
            raw_archivo_final=FOLDER,
            content_type="Reels",
            dry_run=False,
            networks=["instagram"],
        )
        assert downloaded == ["id:VID"]
        downloaded.clear()
        carousel = prepare_media_urls_for_metricool(
            settings=settings,
            metricool=Metricool(),  # type: ignore[arg-type]
            raw_archivo_final=FOLDER,
            content_type="Carrusel",
            dry_run=False,
            networks=["instagram"],
        )
    assert reel == ["https://static.metricool.com/video/1/202610/mp4-1"]
    assert downloaded == ["id:A", "id:B"]
    assert carousel == [
        "https://static.metricool.com/video/1/202610/png-1",
        "https://static.metricool.com/video/1/202610/png-2",
    ]
    assert FOLDER not in reel + carousel


def test_pick_shared_entries_prefers_video_or_carousel_images():
    entries = [
        {".tag": "file", "name": "b.png", "id": "id:B", "size": 2},
        {".tag": "file", "name": "a.png", "id": "id:A", "size": 1},
        {".tag": "file", "name": "reel\u00a0final.mp4", "id": "id:BIG", "size": 50},
        {".tag": "file", "name": "short.mp4", "id": "id:SMALL", "size": 5},
        {".tag": "folder", "name": "nested", "id": "id:DIR"},
    ]
    assert pick_shared_entries(entries, carousel=False, stories=False)[0]["id"] == "id:BIG"
    images = pick_shared_entries(entries, carousel=True, stories=False)
    assert [item["id"] for item in images] == ["id:A", "id:B"]
    assert pick_shared_entries(entries, carousel=False, stories=True)[0]["id"] == "id:BIG"


def test_refresh_token_file_is_read(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "dropbox_refresh_token"
    token_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
    settings = _settings(DROPBOX_REFRESH_TOKEN="", DROPBOX_REFRESH_TOKEN_FILE=str(token_file))
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/oauth2/token"):
            seen["refresh"] = request.content.decode()
            return httpx.Response(400, text="stop")
        return httpx.Response(500)

    with pytest.raises(MediaHostError, match="token refresh"):
        list_shared_folder(
            settings,
            FOLDER,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    assert "refresh_token=from-file" in seen["refresh"]


def test_dropbox_source_does_not_call_temporary_or_shared_link():
    source = Path("src/metricool_sync_posts/media/dropbox_host.py").read_text(encoding="utf-8")
    assert "content.dropboxapi.com/2/files/get_temporary_link" not in source
    assert "create_shared_link" not in source
    assert "litterbox" not in source
