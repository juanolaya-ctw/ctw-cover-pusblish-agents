from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def probe_width(ffprobe_bin: str, path: Path) -> int | None:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width",
        "-of",
        "json",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        data = json.loads(out)
        streams = data.get("streams") or []
        if streams and streams[0].get("width"):
            return int(streams[0]["width"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.debug("ffprobe failed: %s", exc)
    return None


def remux_mov_to_mp4(ffmpeg_bin: str, src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        str(dest),
    ]
    subprocess.check_call(cmd)
    return dest


def scale_video_max_width(ffmpeg_bin: str, src: Path, dest: Path, max_width: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale='min({max_width},iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        str(dest),
    ]
    subprocess.check_call(cmd)
    return dest
