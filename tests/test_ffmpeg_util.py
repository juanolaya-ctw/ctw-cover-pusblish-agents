from pathlib import Path
from unittest.mock import patch

from metricool_sync_posts.media.ffmpeg_util import remux_mov_to_mp4, resolve_ffmpeg


def test_resolve_ffmpeg_none_when_missing():
    with patch("metricool_sync_posts.media.ffmpeg_util.shutil.which", return_value=None):
        with patch(
            "metricool_sync_posts.media.ffmpeg_util.Path.is_file",
            return_value=False,
        ):
            # imageio_ffmpeg may or may not be installed; force ImportError path
            import builtins

            real_import = builtins.__import__

            def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "imageio_ffmpeg":
                    raise ImportError("no imageio")
                return real_import(name, globals, locals, fromlist, level)

            with patch("builtins.__import__", side_effect=fake_import):
                assert resolve_ffmpeg("ffmpeg-definitely-missing") is None


def test_remux_returns_none_without_ffmpeg(tmp_path: Path):
    src = tmp_path / "a.mov"
    src.write_bytes(b"x")
    dest = tmp_path / "a.mp4"
    with patch(
        "metricool_sync_posts.media.ffmpeg_util.resolve_ffmpeg",
        return_value=None,
    ):
        assert remux_mov_to_mp4("ffmpeg", src, dest) is None
    assert not dest.exists()


def test_resolve_ffmpeg_uses_which():
    with patch(
        "metricool_sync_posts.media.ffmpeg_util.shutil.which",
        return_value="/usr/bin/ffmpeg",
    ):
        assert resolve_ffmpeg("ffmpeg") == "/usr/bin/ffmpeg"
