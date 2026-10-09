"""List and download Google Drive folder contents (service account)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.urls import google_drive_direct_url

logger = logging.getLogger(__name__)

DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"

_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)
_VIDEO_MIMES = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-m4v",
    }
)


def _service_account_path(settings: Settings) -> Path | None:
    path = (settings.google_drive_service_account_file or "").strip()
    if path:
        return Path(path)
    return None


def _access_token(settings: Settings) -> str:
    sa_path = _service_account_path(settings)
    if not sa_path or not sa_path.is_file():
        raise RuntimeError(
            "Google Drive folder links require GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE "
            "(JSON key with read access to CTW folders)."
        )
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "Install google-auth for Drive folders: pip install google-auth"
        ) from exc

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path),
        scopes=[DRIVE_READONLY],
    )
    creds.refresh(Request())
    if not creds.token:
        raise RuntimeError("Failed to obtain Google Drive access token")
    return creds.token


def list_folder_files(settings: Settings, folder_id: str) -> list[dict[str, Any]]:
    token = _access_token(settings)
    q = f"'{folder_id}' in parents and trashed = false"
    params = {
        "q": q,
        "fields": "files(id,name,mimeType,size,modifiedTime)",
        "pageSize": 100,
        "orderBy": "name",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    headers = {"Authorization": f"Bearer {token}"}
    files: list[dict[str, Any]] = []
    with httpx.Client(timeout=60.0) as client:
        page_token: str | None = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            resp = client.get(DRIVE_FILES, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            files.extend(data.get("files") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return files


def resolve_folder_to_download_urls(
    settings: Settings,
    folder_id: str,
    *,
    carousel: bool,
    stories: bool,
) -> list[str]:
    """
    Return direct download URLs for files in a Drive folder.

    - carousel / multiple images: all images in folder (max 10)
    - stories: first image or video
    - default: prefer single video; else first file
    """
    entries = list_folder_files(settings, folder_id)
    if not entries:
        raise ValueError(f"Drive folder {folder_id} is empty or not shared with service account")

    def download_url(file_id: str) -> str:
        return google_drive_direct_url(f"https://drive.google.com/file/d/{file_id}/view")

    images = [e for e in entries if e.get("mimeType") in _IMAGE_MIMES]
    videos = [e for e in entries if e.get("mimeType") in _VIDEO_MIMES]

    if carousel and len(images) > 1:
        picked = images[:10]
        logger.info("Drive folder %s: carousel %s images", folder_id, len(picked))
        return [download_url(e["id"]) for e in picked]

    if stories:
        pick = (videos[0] if videos else images[0] if images else entries[0])
        return [download_url(pick["id"])]

    if videos:
        # largest video by size when available
        videos.sort(key=lambda e: int(e.get("size") or 0), reverse=True)
        return [download_url(videos[0]["id"])]

    if images:
        if carousel:
            return [download_url(e["id"]) for e in images[:10]]
        return [download_url(images[0]["id"])]

    return [download_url(entries[0]["id"])]
