"""List and download Google Drive folder contents (service account)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from metricool_sync_posts.config import Settings

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

_CHUNK = 1024 * 1024  # 1 MiB


def _service_account_path(settings: Settings) -> Path | None:
    path = (settings.google_drive_service_account_file or "").strip()
    if path:
        return Path(path)
    return None


def has_drive_service_account(settings: Settings) -> bool:
    path = _service_account_path(settings)
    return bool(path and path.is_file())


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

    # Windows Notepad / PowerShell often write UTF-8 with BOM; google-auth json.load rejects it.
    info = json.loads(sa_path.read_text(encoding="utf-8-sig"))
    creds = service_account.Credentials.from_service_account_info(
        info,
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


def drive_file_ref_url(file_id: str, name: str | None = None) -> str:
    """
    Canonical Drive file URL for the media pipeline.

    Embeds the filename as a trailing path segment so extension_from_url works.
    Download must use the Drive API (alt=media) with the service account — not
    the public uc?export=download HTML interstitial.
    """
    safe = Path(name or "media.bin").name or "media.bin"
    # Keep only a safe basename; quote path-unsafe chars
    safe = re.sub(r"[^\w.\- ()\[\]]+", "_", safe).strip() or "media.bin"
    return f"https://drive.google.com/file/d/{file_id}/{quote(safe, safe='().[] -_')}"


def download_drive_file(
    settings: Settings,
    file_id: str,
    dest: Path,
    *,
    expected_size: int | None = None,
) -> Path:
    """
    Download file bytes via Drive API files.get?alt=media using the service account.

    Streams to disk. Retries with Range resume if the connection drops mid-transfer.
    """
    token = _access_token(settings)
    url = f"{DRIVE_FILES}/{file_id}"
    params = {"alt": "media", "supportsAllDrives": "true"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink(missing_ok=True)

    headers_base = {"Authorization": f"Bearer {token}"}
    max_attempts = 5
    downloaded = 0
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        headers = dict(headers_base)
        mode = "wb"
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"
            mode = "ab"
        try:
            with httpx.stream(
                "GET",
                url,
                params=params,
                headers=headers,
                timeout=600.0,
                follow_redirects=True,
            ) as resp:
                # 200 full body, 206 partial content on resume
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()
                # Reject HTML error pages even with 200
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/html" in ctype:
                    raise RuntimeError(
                        f"Drive API returned HTML for file {file_id} "
                        "(check sharing with the service account)"
                    )
                with dest.open(mode) as f:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
            if expected_size is not None and downloaded < expected_size:
                raise RuntimeError(
                    f"Drive download incomplete for {file_id}: "
                    f"{downloaded}/{expected_size} bytes"
                )
            if downloaded == 0:
                raise RuntimeError(f"Drive API returned empty body for file {file_id}")
            logger.info(
                "Downloaded Drive file %s → %s (%s bytes, attempt %s)",
                file_id,
                dest,
                downloaded,
                attempt,
            )
            return dest
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Drive API download attempt %s/%s for %s failed after %s bytes: %s",
                attempt,
                max_attempts,
                file_id,
                downloaded,
                exc,
            )
            if attempt == max_attempts:
                break
            # keep partial file for Range resume on next attempt

    if dest.exists() and downloaded == 0:
        dest.unlink(missing_ok=True)
    assert last_error is not None
    raise RuntimeError(
        f"Drive API download failed for file {file_id}: {last_error}"
    ) from last_error


def resolve_folder_to_download_urls(
    settings: Settings,
    folder_id: str,
    *,
    carousel: bool,
    stories: bool,
) -> list[str]:
    """
    Return Drive file URLs for files in a folder (downloaded later via SA API).

    - carousel / multiple images: all images in folder (max 10)
    - stories: first image or video
    - default: prefer single video; else first file
    """
    entries = list_folder_files(settings, folder_id)
    if not entries:
        raise ValueError(f"Drive folder {folder_id} is empty or not shared with service account")

    images = [e for e in entries if e.get("mimeType") in _IMAGE_MIMES]
    videos = [e for e in entries if e.get("mimeType") in _VIDEO_MIMES]

    def urls_for(picked: list[dict[str, Any]]) -> list[str]:
        return [drive_file_ref_url(e["id"], e.get("name")) for e in picked]

    if carousel and len(images) > 1:
        picked = images[:10]
        logger.info("Drive folder %s: carousel %s images", folder_id, len(picked))
        return urls_for(picked)

    if stories:
        pick = videos[0] if videos else images[0] if images else entries[0]
        return urls_for([pick])

    if videos:
        # largest video by size when available
        videos.sort(key=lambda e: int(e.get("size") or 0), reverse=True)
        return urls_for([videos[0]])

    if images:
        if carousel:
            return urls_for(images[:10])
        return urls_for([images[0]])

    return urls_for([entries[0]])
