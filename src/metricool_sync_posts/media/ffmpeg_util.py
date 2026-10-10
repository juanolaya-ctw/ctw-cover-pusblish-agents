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


# Instagram Graph reel limits. Re-encode only when one of these is exceeded.
IG_MAX_VIDEO_BITRATE = 25_000_000
IG_MAX_BYTES = 300 * 1024 * 1024


def instagram_reencode_reason(
    *,
    size: int,
    bitrate: int | None,
    width: int | None,
    max_width: int,
) -> str | None:
    """Why an Instagram video must be re-encoded, or None to keep the file."""
    if size > IG_MAX_BYTES:
        return "size"
    if bitrate is not None and bitrate > IG_MAX_VIDEO_BITRATE:
        return "bitrate"
    if width is not None and width > max_width:
        return "width"
    return None


def instagram_reencode_command(
    ffmpeg: str,
    src: Path,
    dest: Path,
    *,
    scale_width: int | None,
) -> list[str]:
    """Target ~14 Mbps. Resolution, fps, and duration stay unless the frame is too wide.

    ``-r`` is omitted on purpose so the source frame rate is kept.
    """
    cmd = [ffmpeg, "-y", "-i", str(src)]
    if scale_width:
        cmd.extend(["-vf", f"scale='min({scale_width},iw)':-2"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "14M",
            "-maxrate",
            "20M",
            "-bufsize",
            "28M",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return cmd


def probe_video(ffprobe_bin: str, path: Path) -> tuple[int | None, int | None]:
    """Return ``(width, video bitrate)``. Bitrate falls back to the format value."""
    resolved = resolve_ffprobe(ffprobe_bin)
    if not resolved:
        logger.warning("ffprobe not found; cannot read bitrate for %s", path.name)
        return None, None
    cmd = [
        resolved,
        "-v",
        "error",
        "-show_entries",
        "stream=width,bit_rate,codec_type",
        "-show_entries",
        "format=bit_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        data = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError, OSError) as exc:
        logger.debug("ffprobe failed: %s", exc)
        return None, None
    width: int | None = None
    stream_rate: int | None = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") not in (None, "video"):
            continue
        if stream.get("width"):
            width = int(stream["width"])
        rate = _positive_int(stream.get("bit_rate"))
        if rate:
            stream_rate = rate
            break
    format_rate = _positive_int((data.get("format") or {}).get("bit_rate"))
    return width, stream_rate or format_rate


def reencode_instagram_video(
    ffmpeg_bin: str,
    src: Path,
    dest: Path,
    *,
    scale_width: int | None,
) -> Path:
    resolved = resolve_ffmpeg(ffmpeg_bin)
    if not resolved:
        raise RuntimeError(
            f"Cannot re-encode {src.name} under Instagram limits: {_FFMPEG_HINT}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = instagram_reencode_command(resolved, src, dest, scale_width=scale_width)
    try:
        subprocess.check_call(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError(_FFMPEG_HINT) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg Instagram re-encode failed for {src}: {exc}") from exc
    return dest


def png_to_jpeg(ffmpeg_bin: str, src: Path, dest: Path) -> Path:
    """``ffmpeg -i cover-final.png -q:v 2 cover-final.jpg``."""
    resolved = resolve_ffmpeg(ffmpeg_bin)
    if not resolved:
        raise RuntimeError(f"Cannot convert {src.name} to JPEG: {_FFMPEG_HINT}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [resolved, "-y", "-i", str(src), "-q:v", "2", str(dest)]
    try:
        subprocess.check_call(cmd)
    except FileNotFoundError as exc:
        raise RuntimeError(_FFMPEG_HINT) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg JPEG conversion failed for {src}: {exc}") from exc
    return dest


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
