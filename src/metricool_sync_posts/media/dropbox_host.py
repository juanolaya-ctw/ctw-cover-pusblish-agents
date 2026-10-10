"""Read files out of a Dropbox shared folder.

Folder links (``/scl/fo/`` and ``/sh/``) with ``dl=1`` download a ZIP, not the
video. Listing uses ``files/list_folder`` and each file is downloaded by its
Dropbox id (``id:...``). Paths are not used: names can contain a non-breaking
space (U+00A0). ``sharing.write`` and ``files/get_temporary_link`` are not used.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.errors import MediaHostError

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
_LIST_URL = "https://api.dropboxapi.com/2/files/list_folder"
_LIST_CONTINUE_URL = "https://api.dropboxapi.com/2/files/list_folder/continue"
_DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
_DEFAULT_REFRESH_FILE = "/home/box/.secrets/dropbox_refresh_token"

_IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
_VIDEO_EXT = frozenset({".mp4", ".mov", ".m4v", ".webm"})


def dropbox_configured(settings: Settings) -> bool:
    return _credentials(settings) is not None


def list_shared_folder(
    settings: Settings,
    folder_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Every file entry in a shared folder, including nested files one level down."""
    token, http, own = _open(settings, client)
    try:
        entries = _list_path(http, token, folder_url, path="")
        files = [entry for entry in entries if _is_file(entry)]
        if files:
            return files
        nested: list[dict] = []
        for entry in entries:
            if str(entry.get(".tag") or "") != "folder":
                continue
            folder_path = str(entry.get("path_lower") or entry.get("path_display") or "")
            if not folder_path:
                continue
            nested.extend(
                item
                for item in _list_path(http, token, folder_url, path=folder_path)
                if _is_file(item)
            )
        return nested
    finally:
        if own:
            http.close()


def download_shared_file(
    settings: Settings,
    file_id: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download one shared file by Dropbox id (``id:...``), never by path."""
    ident = (file_id or "").strip()
    if not ident.startswith("id:"):
        raise MediaHostError(
            f"Dropbox download needs a file id (id:...), not a path ({ident!r}). "
            "Folder names can contain non-breaking spaces, so the path is not used."
        )
    token, http, own = _open(settings, client)
    dest.parent.mkdir(parents=True, exist_ok=True)
    arg = json.dumps({"path": ident}, separators=(",", ":"))
    try:
        with http.stream(
            "POST",
            _DOWNLOAD_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Dropbox-API-Arg": arg,
            },
        ) as response:
            if response.status_code >= 400:
                body = response.read()
                snippet = " ".join(body.decode("utf-8", errors="replace").split())[:300]
                raise MediaHostError(
                    f"Dropbox files/download of {ident} failed ({response.status_code}): {snippet}"
                )
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
    except MediaHostError:
        raise
    except httpx.HTTPError as exc:
        raise MediaHostError(f"Dropbox files/download of {ident} failed: {exc}") from exc
    finally:
        if own:
            http.close()
    if dest.stat().st_size == 0:
        raise MediaHostError(f"Dropbox files/download of {ident} returned an empty file")
    logger.info("Downloaded Dropbox file %s → %s", ident, dest.name)
    return dest


def pick_shared_entries(
    entries: list[dict],
    *,
    carousel: bool,
    stories: bool,
) -> list[dict]:
    """Choose the video (reel/post) or the images (carousel) inside a folder."""
    files = [entry for entry in entries if _is_file(entry) and entry.get("id")]
    if not files:
        raise MediaHostError(
            "Dropbox folder has no files. A folder shared link downloads as a ZIP; "
            "the video or images inside it have to be uploaded individually."
        )
    images = [entry for entry in files if _ext(entry) in _IMAGE_EXT]
    videos = [entry for entry in files if _ext(entry) in _VIDEO_EXT]
    images.sort(key=lambda entry: str(entry.get("name") or "").lower())
    videos.sort(key=lambda entry: int(entry.get("size") or 0), reverse=True)

    if carousel and len(images) > 1:
        return images[:10]
    if stories:
        if videos:
            return [videos[0]]
        if images:
            return [images[0]]
        return [files[0]]
    if videos:
        return [videos[0]]
    if images:
        return images[:10] if carousel else [images[0]]
    raise MediaHostError(
        "Dropbox folder has no video or image. Refusing to schedule the folder ZIP."
    )


def _open(
    settings: Settings,
    client: httpx.Client | None,
) -> tuple[str, httpx.Client, bool]:
    creds = _credentials(settings)
    if creds is None:
        raise MediaHostError(
            "Dropbox folder link needs DROPBOX_APP_KEY, DROPBOX_APP_SECRET, and "
            "DROPBOX_REFRESH_TOKEN (or /home/box/.secrets/dropbox_refresh_token) "
            "to download the file inside the folder. A /scl/fo/ link with dl=1 "
            "returns a ZIP, not the video. sharing.write is not required."
        )
    own = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0))
    try:
        token = _access_token(http, *creds)
    except Exception:
        if own:
            http.close()
        raise
    return token, http, own


def _credentials(settings: Settings) -> tuple[str, str, str] | None:
    refresh = (settings.dropbox_refresh_token or "").strip()
    if not refresh:
        file_path = (settings.dropbox_refresh_token_file or "").strip() or _DEFAULT_REFRESH_FILE
        candidate = Path(file_path)
        if candidate.is_file():
            refresh = candidate.read_text(encoding="utf-8").strip()
    app_key = (settings.dropbox_app_key or "").strip()
    app_secret = (settings.dropbox_app_secret or "").strip()
    if refresh and app_key and app_secret:
        return refresh, app_key, app_secret
    return None


def _access_token(http: httpx.Client, refresh: str, app_key: str, app_secret: str) -> str:
    try:
        response = http.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": app_key,
                "client_secret": app_secret,
            },
        )
    except httpx.HTTPError as exc:
        raise MediaHostError(f"Dropbox token refresh failed: {exc}") from exc
    if response.status_code >= 400:
        snippet = " ".join((response.text or "").split())[:240]
        raise MediaHostError(f"Dropbox token refresh failed ({response.status_code}): {snippet}")
    token = str((response.json() or {}).get("access_token") or "").strip()
    if not token:
        raise MediaHostError("Dropbox token refresh returned no access_token")
    return token


def _list_path(http: httpx.Client, token: str, folder_url: str, *, path: str) -> list[dict]:
    payload: dict = {"path": path, "shared_link": {"url": folder_url}}
    url = _LIST_URL
    entries: list[dict] = []
    while True:
        response = http.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        if response.status_code >= 400:
            snippet = " ".join((response.text or "").split())[:300]
            raise MediaHostError(
                f"Dropbox files/list_folder failed ({response.status_code}): {snippet}"
            )
        data = response.json() if response.content else {}
        if not isinstance(data, dict):
            raise MediaHostError("Dropbox files/list_folder returned a non-object body")
        batch = data.get("entries") or []
        if isinstance(batch, list):
            entries.extend(item for item in batch if isinstance(item, dict))
        if not data.get("has_more"):
            return entries
        cursor = str(data.get("cursor") or "").strip()
        if not cursor:
            raise MediaHostError("Dropbox files/list_folder has_more without a cursor")
        url = _LIST_CONTINUE_URL
        payload = {"cursor": cursor}


def _is_file(entry: dict) -> bool:
    return str(entry.get(".tag") or "") == "file"


def _ext(entry: dict) -> str:
    name = str(entry.get("name") or "")
    return Path(name).suffix.lower()
