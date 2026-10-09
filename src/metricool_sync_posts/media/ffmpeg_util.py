from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_FFMPEG_HINT = (
    "ffmpeg not found. On Windows install with: "
    "winget install --id Gyan.FFmpeg -e  "
    "or: pip install imageio-ffmpeg  "
    "then re-run (or set FFMPEG_BIN to the ffmpeg.exe path)."
)


def resolve_ffmpeg(configured: str = "ffmpeg") -> str | None:
    """Return an executable ffmpeg path, or None if unavailable."""
    if configured and Path(configured).is_file():
        return configured
    which = shutil.which(configured or "ffmpeg")
    if which:
        return which
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            logger.info("Using imageio-ffmpeg binary: %s", exe)
            return exe
    except Exception as exc:
        logger.debug("imageio-ffmpeg unavailable: %s", exc)
    return None


def resolve_ffprobe(configured: str = "ffprobe") -> str | None:
    if configured and Path(configured).is_file():
        return configured
    which = shutil.which(configured or "ffprobe")
    if which:
        return which
    # imageio-ffmpeg ships ffmpeg only; probe may still be missing
    return None


def probe_width(ffprobe_bin: str, path: Path) -> int | None:
    resolved = resolve_ffprobe(ffprobe_bin)
    if not resolved:
        logger.warning("ffprobe not found; skipping width probe for %s", path.name)
        return None
    cmd = [
        resolved,
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


def remux_mov_to_mp4(ffmpeg_bin: str, src: Path, dest: Path) -> Path | None:
    """
    Remux .mov → .mp4 with stream copy.

    Returns dest on success, None if ffmpeg is missing (caller may pass .mov through).
    Raises RuntimeError with install hint only when ffmpeg was found but failed.
    """
    resolved = resolve_ffmpeg(ffmpeg_bin)
    if not resolved:
        logger.warning(
            "Cannot remux %s → mp4: %s Passing .mov through to Metricool.",
            src.name,
            _FFMPEG_HINT,
        )
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolved,
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        str(dest),
    ]
    try:
        subprocess.check_call(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError(_FFMPEG_HINT) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg remux failed for {src}: {exc}") from exc
    return dest


def scale_video_max_width(
    ffmpeg_bin: str, src: Path, dest: Path, max_width: int
) -> Path | None:
    resolved = resolve_ffmpeg(ffmpeg_bin)
    if not resolved:
        logger.warning(
            "Cannot scale %s: %s Leaving original resolution.",
            src.name,
            _FFMPEG_HINT,
        )
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        resolved,
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
    try:
        subprocess.check_call(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError(_FFMPEG_HINT) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg scale failed for {src}: {exc}") from exc
    return dest
