"""Classify Notion Archivo Final URLs for scheduling."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import urlparse


class FinalFileKind(enum.Enum):
    YOUTUBE = "youtube"
    DROPBOX = "dropbox"
    GOOGLE_DRIVE_FILE = "google_drive_file"
    GOOGLE_DRIVE_FOLDER = "google_drive_folder"
    OTHER_URL = "other_url"
    EMPTY = "empty"
    # Plain text, a host with no scheme, or any other value httpx cannot request.
    INVALID = "invalid"


_HTTP_SCHEMES = frozenset({"http", "https"})
_PREVIEW_LIMIT = 120
_ARCHIVO_FINAL_HINT = (
    '"Archivo Final" no tiene un link. Pega el link de la carpeta o archivo '
    "de Drive o Dropbox (o del video de YouTube)."
)


_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_DRIVE_FOLDER_RE = re.compile(r"/(?:drive/)?folders/([a-zA-Z0-9_-]+)")
_DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


@dataclass(frozen=True)
class FinalFileRef:
    kind: FinalFileKind
    raw_url: str
    drive_folder_id: str | None = None
    drive_file_id: str | None = None


def is_http_url(url: str | None) -> bool:
    """True when ``url`` is an absolute http(s) URL with a host.

    Notion's URL property stores any string. A clip title, an empty cell, or
    ``drive.google.com/...`` without a scheme makes httpx raise
    ``Request URL is missing an 'http://' or 'https://' protocol``.
    """
    if not url or not str(url).strip():
        return False
    parsed = urlparse(str(url).strip())
    return parsed.scheme.lower() in _HTTP_SCHEMES and bool(parsed.netloc)


def archivo_final_preview(value: str | None, *, limit: int = _PREVIEW_LIMIT) -> str:
    """Single-line preview of the current Archivo Final value."""
    text = " ".join((value or "").split())
    if not text:
        return "(vacío)"
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def invalid_archivo_final_message(page_url: str, raw: str | None) -> str:
    """Slack text for a row whose Archivo Final is not an http(s) link."""
    page = (page_url or "").strip() or "(sin enlace de Notion)"
    return f"{_ARCHIVO_FINAL_HINT}\n{page}\nContiene: {archivo_final_preview(raw)}"


def classify_final_file(url: str | None) -> FinalFileRef:
    if not url or not str(url).strip():
        return FinalFileRef(kind=FinalFileKind.EMPTY, raw_url="")

    raw = str(url).strip()
    if not is_http_url(raw):
        return FinalFileRef(kind=FinalFileKind.INVALID, raw_url=raw)

    host = urlparse(raw).netloc.lower()

    if any(h in host for h in _YOUTUBE_HOSTS):
        return FinalFileRef(kind=FinalFileKind.YOUTUBE, raw_url=raw)

    if "dropbox.com" in host:
        return FinalFileRef(kind=FinalFileKind.DROPBOX, raw_url=raw)

    if "drive.google.com" in host or "docs.google.com" in host:
        m_folder = _DRIVE_FOLDER_RE.search(raw)
        if m_folder:
            return FinalFileRef(
                kind=FinalFileKind.GOOGLE_DRIVE_FOLDER,
                raw_url=raw,
                drive_folder_id=m_folder.group(1),
            )
        m_file = _DRIVE_FILE_RE.search(raw)
        if m_file:
            return FinalFileRef(
                kind=FinalFileKind.GOOGLE_DRIVE_FILE,
                raw_url=raw,
                drive_file_id=m_file.group(1),
            )

    return FinalFileRef(kind=FinalFileKind.OTHER_URL, raw_url=raw)
