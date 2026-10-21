"""Draw the Titulo hook on a local reel still.

The cover agent is preferred when it can take the frame (see ``bridge``).
This is the entry point used when that agent has no local-frame renderer:
center-crop 9:16 (or any still) to a 4:5 1080x1350 PNG and paint the hook
on the lower third. The schedule job converts that PNG to JPEG.
"""

from __future__ import annotations

import logging
import struct
import subprocess
from pathlib import Path

from metricool_sync_posts.media.ffmpeg_util import resolve_ffmpeg

logger = logging.getLogger(__name__)

COVER_WIDTH = 1080
COVER_HEIGHT = 1350
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_MARGIN_X = 72
_MIN_FONT = 42
_MAX_FONT = 84
_MAX_LINES = 5
_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/macos/Inter-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/Arialbd.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG IHDR, or None when ``data`` is not a PNG."""
    if len(data) < 24 or not data.startswith(_PNG_SIG) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def layout_hook(text: str) -> tuple[int, list[str]]:
    """Font size and wrapped lines that fit the 4:5 cover."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return _MAX_FONT, []
    max_width = COVER_WIDTH - 2 * _MARGIN_X
    for size in range(_MAX_FONT, _MIN_FONT - 1, -2):
        lines = _wrap(cleaned, size, max_width)
        if len(lines) <= _MAX_LINES:
            return size, lines
    return _MIN_FONT, _wrap(cleaned, _MIN_FONT, max_width)[:_MAX_LINES]


def find_hook_font(agent_root: str | None = None) -> Path | None:
    """A bold face shipped with the cover agent, else a system sans."""
    root = Path(agent_root) if agent_root else None
    if root is not None and root.is_dir():
        for folder in (root / "fonts", root / "assets" / "fonts", root / "assets"):
            found = _bold_font_in(folder)
            if found is not None:
                return found
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def render_hook_cover(
    *,
    ffmpeg_bin: str,
    titulo: str,
    frame_path: Path,
    dest: Path,
    agent_root: str | None = None,
) -> bytes:
    """Center-crop ``frame_path`` to 1080x1350 and draw ``titulo``.

    Raises RuntimeError when ffmpeg, the font, or the still cannot produce
    that PNG. The caller turns that into a skip and does not upload the still.
    """
    text = " ".join((titulo or "").split())
    if not text:
        raise RuntimeError("Titulo hook is empty")
    ffmpeg = resolve_ffmpeg(ffmpeg_bin)
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not available to render the reel cover")
    font = find_hook_font(agent_root)
    if font is None:
        raise RuntimeError("no bold font found to draw the Titulo hook")
    size, lines = layout_hook(text)
    if not lines:
        raise RuntimeError("Titulo hook is empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    text_file = dest.with_name("cover-hook.txt")
    text_file.write_text("\n".join(lines), encoding="utf-8")
    try:
        _run_ffmpeg(
            ffmpeg,
            frame_path,
            dest,
            vf=_filter(font, text_file, size, len(lines)),
        )
        data = dest.read_bytes()
    finally:
        text_file.unlink(missing_ok=True)
    if png_size(data) != (COVER_WIDTH, COVER_HEIGHT):
        raise RuntimeError(
            f"rendered cover is not {COVER_WIDTH}x{COVER_HEIGHT} "
            f"(got {png_size(data)})"
        )
    return data


def _bold_font_in(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    faces = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf"}
    ]
    bold = [path for path in faces if "bold" in path.name.lower()]
    chosen = sorted(bold or faces, key=lambda path: path.name.lower())
    return chosen[0] if chosen else None


def _wrap(text: str, fontsize: int, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if _text_width(trial, fontsize) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def _text_width(text: str, fontsize: int) -> float:
    # Inter Bold is a bit wider than half the em. Conservative so lines stay inside.
    return len(text) * fontsize * 0.56


def _filter(font: Path, text_file: Path, fontsize: int, line_count: int) -> str:
    line_gap = 12
    block = (fontsize + line_gap) * line_count - line_gap
    bottom = 110
    y = COVER_HEIGHT - bottom - block
    if y < int(COVER_HEIGHT * 0.42):
        y = int(COVER_HEIGHT * 0.42)
    scrim_y = max(0, y - 40)
    font_arg = _filter_path(font)
    text_arg = _filter_path(text_file)
    return (
        f"scale={COVER_WIDTH}:{COVER_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={COVER_WIDTH}:{COVER_HEIGHT},"
        f"drawbox=x=0:y={scrim_y}:w=iw:h=ih-{scrim_y}:color=black@0.45:t=fill,"
        f"drawtext=fontfile='{font_arg}':textfile='{text_arg}':expansion=none:"
        f"fontsize={fontsize}:fontcolor=white:borderw=4:bordercolor=black:"
        f"line_spacing={line_gap}:x=(w-text_w)/2:y={y}"
    )


def _filter_path(path: Path) -> str:
    text = path.resolve().as_posix().replace("'", r"\'").replace(":", r"\:")
    return text.replace(",", r"\,")


def _run_ffmpeg(ffmpeg: str, src: Path, dest: Path, *, vf: str) -> None:
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-update",
        "1",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(f"ffmpeg cover render failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg cover render timed out") from exc
