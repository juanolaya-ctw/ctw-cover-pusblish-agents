from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.download import download_to_path
from metricool_sync_posts.media.drive_folder import (
    download_drive_file,
    drive_file_ref_url,
    resolve_folder_to_download_urls,
)
from metricool_sync_posts.media.urls import extract_drive_file_id


def _settings(tmp_path: Path, *, sa: bool = True) -> Settings:
    sa_path = tmp_path / "sa.json"
    if sa:
        sa_path.write_text(
            '{"type":"service_account","client_email":"sa@test.iam.gserviceaccount.com",'
            '"token_uri":"https://oauth2.googleapis.com/token","private_key":"x"}',
            encoding="utf-8",
        )
    return Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE=str(sa_path) if sa else "",
        TRANSFER_SH=True,
    )


def test_extract_drive_file_id():
    assert (
        extract_drive_file_id("https://drive.google.com/file/d/1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb/view")
        == "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb"
    )
    assert (
        extract_drive_file_id(
            "https://drive.google.com/uc?export=download&id=1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb"
        )
        == "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb"
    )
    assert extract_drive_file_id("https://drive.google.com/drive/folders/abc") is None


def test_drive_file_ref_url_keeps_extension():
    url = drive_file_ref_url("1abc", "clip.mp4")
    assert "1abc" in url
    assert url.endswith("clip.mp4") or "clip.mp4" in url


def test_download_drive_file_uses_alt_media(tmp_path: Path):
    settings = _settings(tmp_path)
    dest = tmp_path / "out.bin"
    payload = b"\x00\x01FAKEMP4DATA"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mp4"}
    mock_resp.iter_bytes = MagicMock(return_value=iter([payload]))
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    with (
        patch(
            "metricool_sync_posts.media.drive_folder._access_token",
            return_value="tok",
        ),
        patch(
            "metricool_sync_posts.media.drive_folder.httpx.stream",
            return_value=mock_resp,
        ) as stream,
    ):
        download_drive_file(settings, "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb", dest)

    assert dest.read_bytes() == payload
    kwargs = stream.call_args.kwargs
    assert kwargs["params"]["alt"] == "media"
    assert "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb" in stream.call_args.args[1]


def test_download_to_path_prefers_sa_api_over_uc(tmp_path: Path):
    settings = _settings(tmp_path)
    dest = tmp_path / "media.mp4"
    url = drive_file_ref_url("1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb", "reel.mp4")

    with patch(
        "metricool_sync_posts.media.download.download_drive_file",
        return_value=dest,
    ) as api_dl:
        download_to_path(url, dest, settings=settings)

    api_dl.assert_called_once()
    assert api_dl.call_args.args[1] == "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb"


def test_download_to_path_rejects_uc_html_without_sa(tmp_path: Path):
    settings = _settings(tmp_path, sa=False)
    dest = tmp_path / "media.bin"
    url = "https://drive.google.com/file/d/1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb/view"

    html = b"<!DOCTYPE html><html><body>Sign in</body></html>"

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            return None

        @property
        def headers(self):
            return {"content-type": "text/html; charset=utf-8"}

        def iter_bytes(self):
            yield html

    with patch("metricool_sync_posts.media.download.httpx.stream", return_value=_Stream()):
        with pytest.raises(RuntimeError, match="HTML instead of a file"):
            download_to_path(url, dest, settings=settings)


def test_resolve_folder_returns_file_refs_not_uc(tmp_path: Path):
    settings = _settings(tmp_path)
    entries = [
        {
            "id": "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb",
            "name": "reel.mp4",
            "mimeType": "video/mp4",
            "size": "1000",
        }
    ]
    with patch(
        "metricool_sync_posts.media.drive_folder.list_folder_files",
        return_value=entries,
    ):
        urls = resolve_folder_to_download_urls(
            settings, "folder1", carousel=False, stories=False
        )
    assert len(urls) == 1
    assert "uc?export=download" not in urls[0]
    assert "1oMO6fXR27ytuVdNia_xUQfVpfn8vCReb" in urls[0]
    assert "reel.mp4" in urls[0]


def test_transfer_sh_alias():
    s = Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        TRANSFER_SH=True,
    )
    assert s.transfer_sh_enabled is True
