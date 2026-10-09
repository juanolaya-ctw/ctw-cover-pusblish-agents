from pathlib import Path
from unittest.mock import patch

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.upload_public import upload_public_url


def _settings(**kwargs) -> Settings:
    base = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        TRANSFER_SH=False,
    )
    base.update(kwargs)
    return Settings(**base)


def test_upload_falls_back_to_litterbox_when_transfer_disabled(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake")
    settings = _settings()

    with patch(
        "metricool_sync_posts.media.upload_public._upload_litterbox",
        return_value="https://litter.catbox.moe/abc.mp4",
    ) as lit:
        url = upload_public_url(settings, path)

    assert url == "https://litter.catbox.moe/abc.mp4"
    lit.assert_called_once_with(path)


def test_transfer_sh_optional_then_fallback(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake")
    settings = _settings(TRANSFER_SH=True)

    with (
        patch(
            "metricool_sync_posts.media.upload_public._upload_transfer_sh",
            side_effect=RuntimeError("ConnectTimeout"),
        ),
        patch(
            "metricool_sync_posts.media.upload_public._upload_litterbox",
            side_effect=RuntimeError("down"),
        ),
        patch(
            "metricool_sync_posts.media.upload_public._upload_uguu",
            return_value="https://h.uguu.se/x.mp4",
        ) as uguu,
    ):
        url = upload_public_url(settings, path)

    assert url == "https://h.uguu.se/x.mp4"
    uguu.assert_called_once()


def test_s3_preferred_when_configured(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake")
    settings = _settings(
        S3_ENDPOINT="https://s3.example",
        S3_BUCKET="b",
        S3_ACCESS_KEY="a",
        S3_SECRET_KEY="s",
        S3_PUBLIC_BASE_URL="https://cdn.example",
    )
    with patch(
        "metricool_sync_posts.media.upload_public._upload_s3",
        return_value="https://cdn.example/clip.mp4",
    ) as s3:
        url = upload_public_url(settings, path)
    assert url.endswith("clip.mp4")
    s3.assert_called_once()


def test_all_hosts_fail_raises(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake")
    settings = _settings()
    with (
        patch(
            "metricool_sync_posts.media.upload_public._upload_litterbox",
            side_effect=RuntimeError("a"),
        ),
        patch(
            "metricool_sync_posts.media.upload_public._upload_uguu",
            side_effect=RuntimeError("b"),
        ),
    ):
        with pytest.raises(RuntimeError, match="All public upload hosts failed"):
            upload_public_url(settings, path)
