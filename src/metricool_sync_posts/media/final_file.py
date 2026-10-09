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


_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_DRIVE_FOLDER_RE = re.compile(r"/(?:drive/)?folders/([a-zA-Z0-9_-]+)")
_DRIVE_FILE_RE = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")


@dataclass(frozen=True)
class FinalFileRef:
    kind: FinalFileKind
    raw_url: str
    drive_folder_id: str | None = None
    drive_file_id: str | None = None


def classify_final_file(url: str | None) -> FinalFileRef:
    if not url or not url.strip():
        return FinalFileRef(kind=FinalFileKind.EMPTY, raw_url="")

    raw = url.strip()
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
