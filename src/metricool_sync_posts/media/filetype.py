"""Detect image/video type from magic bytes and Drive mimeType.

Uploaded names must never be ``.bin``. Drive sometimes stores a file as
``application/octet-stream`` with a ``.bin`` name; the bytes (or mimeType)
still say png/jpeg/webp/mp4/mov.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from metricool_sync_posts.media.errors import MediaHostError

_HEADER = 64

# ftyp brands that are not MP4. QuickTime is handled separately.
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"mif1", b"msf1"})
_MOV_BRANDS = frozenset({b"qt  "})
_MOV_ATOMS = frozenset({b"moov", b"mdat", b"wide", b"free", b"pnot", b"skip", b"junk"})

_MIME: dict[str, tuple[str, str]] = {
    "image/png": (".png", "image/png"),
    "image/jpeg": (".jpg", "image/jpeg"),
    "image/jpg": (".jpg", "image/jpeg"),
    "image/webp": (".webp", "image/webp"),
    "image/gif": (".gif", "image/gif"),
    "video/mp4": (".mp4", "video/mp4"),
    "video/quicktime": (".mov", "video/quicktime"),
    "video/webm": (".webm", "video/webm"),
    "video/x-m4v": (".m4v", "video/mp4"),
}

# .jpeg and .jpg are the same file type for naming.
_EXT_ALIAS = {".jpeg": ".jpg"}


@dataclass(frozen=True)
class MediaType:
    ext: str
    content_type: str

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")

    @property
    def is_video(self) -> bool:
        return self.content_type.startswith("video/")


def media_type_from_mime(mime: str | None) -> MediaType | None:
    key = (mime or "").split(";")[0].strip().lower()
    pair = _MIME.get(key)
    if pair is None:
        return None
    return MediaType(pair[0], pair[1])


def media_type_from_content_type(value: str | None) -> MediaType | None:
    return media_type_from_mime(value)


def content_type_is_media(value: str | None) -> bool:
    ctype = (value or "").split(";")[0].strip().lower()
    return ctype.startswith("image/") or ctype.startswith("video/")


def sniff_header(header: bytes, *, mime: str | None = None) -> MediaType | None:
    """Magic bytes win. Drive mimeType is the fallback when the header is unknown."""
    kind = _from_magic(header)
    if kind is not None:
        return kind
    return media_type_from_mime(mime)


def sniff_file(path: Path, *, mime: str | None = None) -> MediaType | None:
    with path.open("rb") as handle:
        header = handle.read(_HEADER)
    return sniff_header(header, mime=mime)


def ensure_media_suffix(path: Path, *, mime: str | None = None) -> tuple[Path, MediaType]:
    """Rename ``path`` so its suffix matches the detected type. Never leave ``.bin``."""
    kind = sniff_file(path, mime=mime)
    if kind is None or kind.ext == ".bin":
        raise MediaHostError(
            f"Could not detect png/jpg/webp/mp4/mov for {path.name} "
            f"(mime={mime or '-'}) ; refusing to upload a .bin"
        )
    current = _EXT_ALIAS.get(path.suffix.lower(), path.suffix.lower())
    wanted = _EXT_ALIAS.get(kind.ext, kind.ext)
    if current == wanted:
        return path, kind
    dest = path.with_suffix(kind.ext)
    if dest.exists() and dest != path:
        dest.unlink()
    path.replace(dest)
    return dest, kind


def _from_magic(header: bytes) -> MediaType | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaType(".png", "image/png")
    if header.startswith(b"\xff\xd8\xff"):
        return MediaType(".jpg", "image/jpeg")
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return MediaType(".webp", "image/webp")
    if header.startswith((b"GIF87a", b"GIF89a")):
        return MediaType(".gif", "image/gif")
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return MediaType(".webm", "video/webm")
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in _HEIF_BRANDS:
            return None
        if brand in _MOV_BRANDS:
            return MediaType(".mov", "video/quicktime")
        return MediaType(".mp4", "video/mp4")
    if len(header) >= 8 and header[4:8] in _MOV_ATOMS:
        return MediaType(".mov", "video/quicktime")
    return None
