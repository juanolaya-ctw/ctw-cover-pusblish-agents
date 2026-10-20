"""Read files out of a Dropbox shared folder.

Folder links (``/scl/fo/`` and ``/sh/``) with ``dl=1`` download a ZIP, not the
video. Listing uses ``files/list_folder`` with ``shared_link``. Entries from a
folder owned by another account have no ``path_lower``; an empty path without
``shared_link`` would list our account root, so the relative path is built from
each entry's ``name``.

Those files are downloaded with ``sharing/get_shared_link_file``
(``Dropbox-API-Arg`` ``url`` + ``path`` relative to the shared folder). Files
mounted in our own Dropbox (``path_lower`` set) still use ``files/download`` by
id. ``/scl/fi/`` single-file links are not handled here. ``sharing.write`` and
``files/get_temporary_link`` are not used.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.urls import is_dropbox_folder_url

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
_LIST_URL = "https://api.dropboxapi.com/2/files/list_folder"
_LIST_CONTINUE_URL = "https://api.dropboxapi.com/2/files/list_folder/continue"
_DOWNLOAD_URL = "https://content.dropboxapi.com/2/files/download"
_SHARED_LINK_FILE_URL = "https://content.dropboxapi.com/2/sharing/get_shared_link_file"
_SHARED_LINK_METADATA_URL = "https://api.dropboxapi.com/2/sharing/get_shared_link_metadata"
_DEFAULT_REFRESH_FILE = "/home/box/.secrets/dropbox_refresh_token"

_IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
_VIDEO_EXT = frozenset({".mp4", ".mov", ".m4v", ".webm"})

SHARING_READ_REASON = "dropbox_sharing_read"
FILE_NOT_FOUND_REASON = "dropbox_file_not_found"

SHARING_READ_MESSAGE = (
    "La app de Dropbox no tiene el permiso sharing.read; "
    "no se pudo leer la carpeta compartida"
)
SHARING_READ_FIX = (
    "Cómo arreglar: activa sharing.read en la app de Dropbox y reautoriza, "
    "o pega un link directo al archivo."
)
NO_COVER_IN_FOLDER = (
    "No hay imagen de portada en la carpeta compartida de Dropbox. "
    "El archivo no existe en esa carpeta."
)

_FOLD = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
        "\u2007": " ",
        "\u2009": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2032": "'",
        "\u00ab": '"',
        "\u00bb": '"',
    }
)
_STILL_SUFFIX = re.compile(r"(?:[.\s_\-]+)still\d*$", re.IGNORECASE)
_SECRET = re.compile(
    r"(?i)(rlkey=)[^&\s\"']+|(access_token|refresh_token|client_secret)(\s*[:=]\s*)\S+|bearer\s+\S+"
)


@dataclass(frozen=True)
class DropboxCheck:
    """Result of ``dropbox-check``. ``message`` never contains tokens."""

    ok: bool
    message: str
    file_count: int = 0
    names: tuple[str, ...] = ()


def sharing_read_text() -> str:
    return f"{SHARING_READ_MESSAGE}. {SHARING_READ_FIX}"


def file_not_found_text(path: str) -> str:
    shown = path or "solicitado"
    return f"El archivo {shown} no existe en la carpeta compartida de Dropbox."


def scrub_secrets(text: str) -> str:
    """Drop refresh tokens, access tokens, and ``rlkey`` values from text."""

    def _replace(match: re.Match[str]) -> str:
        if match.group(1):
            return "rlkey=…"
        if match.group(2):
            return f"{match.group(2)}{match.group(3)}…"
        return "bearer …"

    return _SECRET.sub(_replace, text)


def dropbox_configured(settings: Settings) -> bool:
    return _credentials(settings) is not None


def list_shared_folder(
    settings: Settings,
    folder_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Every file entry in a shared folder, including nested files one level down.

    The path sent with ``shared_link`` is relative to that folder (``/name``),
    never our account root and never ``path_lower``.
    """
    token, http, own = _open(settings, client)
    try:
        return _list_shared_files(http, token, folder_url)
    finally:
        if own:
            http.close()


def find_shared_cover(
    settings: Settings,
    folder_url: str,
    *,
    client: httpx.Client | None = None,
) -> dict | None:
    """Pick the reel still inside a shared folder.

    Listing always sends ``shared_link``. A missing ``path_lower`` does not
    fall back to the account root. Returns None when the folder has no image.
    """
    token, http, own = _open(settings, client)
    try:
        files, folders = _files_under(http, token, folder_url, "")
        images = [item for item in files if _ext(item) in _IMAGE_EXT]
        videos = [item for item in files if _ext(item) in _VIDEO_EXT]
        if not images:
            for folder in folders:
                name = str(folder.get("name") or "").strip()
                if not name:
                    continue
                nested, _deeper = _files_under(
                    http,
                    token,
                    folder_url,
                    shared_relative_path("", name),
                )
                images.extend(item for item in nested if _ext(item) in _IMAGE_EXT)
                videos.extend(item for item in nested if _ext(item) in _VIDEO_EXT)
        return pick_cover_image(images, video_name=_largest_video_name(videos))
    finally:
        if own:
            http.close()


def download_shared_link_file(
    settings: Settings,
    shared_url: str,
    relative_path: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Stream one file inside a shared folder via ``sharing/get_shared_link_file``."""
    path = relative_path if relative_path.startswith("/") else f"/{relative_path}"
    arg = _api_arg({"url": shared_url, "path": path})
    token, http, own = _open(settings, client)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _stream_download(
            http,
            token,
            _SHARED_LINK_FILE_URL,
            arg,
            dest,
            action=f"Dropbox sharing/get_shared_link_file of {path}",
            missing_path=path,
        )
    finally:
        if own:
            http.close()
    logger.info("Downloaded Dropbox shared file %s → %s", path, dest.name)
    return dest


def download_shared_file(
    settings: Settings,
    file_id: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download one file in our Dropbox by id (``id:...``), never by path."""
    ident = (file_id or "").strip()
    if not ident.startswith("id:"):
        raise MediaHostError(
            f"Dropbox download needs a file id (id:...), not a path ({ident!r}). "
            "Folder names can contain non-breaking spaces, so the path is not used."
        )
    arg = _api_arg({"path": ident})
    token, http, own = _open(settings, client)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _stream_download(
            http,
            token,
            _DOWNLOAD_URL,
            arg,
            dest,
            action=f"Dropbox files/download of {ident}",
            missing_path=ident,
        )
    finally:
        if own:
            http.close()
    logger.info("Downloaded Dropbox file %s → %s", ident, dest.name)
    return dest


def download_shared_entry(
    settings: Settings,
    shared_url: str,
    entry: dict,
    dest: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download a listed entry: by id when it is in our account, else by shared link."""
    if entry_in_account(entry):
        file_id = str(entry.get("id") or "").strip()
        if file_id.startswith("id:"):
            return download_shared_file(settings, file_id, dest, client=client)
    relative = str(entry.get("relative_path") or "").strip()
    if not relative:
        name = str(entry.get("name") or "").strip()
        if not name:
            raise MediaHostError("Dropbox shared file has no name or relative path")
        relative = shared_relative_path("", name)
    return download_shared_link_file(settings, shared_url, relative, dest, client=client)


def check_shared_folder(
    settings: Settings,
    shared_url: str,
    *,
    client: httpx.Client | None = None,
) -> DropboxCheck:
    """Confirm the token has ``sharing.read`` and can list ``shared_url``.

    The result message does not include the access token, refresh token, app
    secret, or ``rlkey``.
    """
    url = (shared_url or "").strip()
    if not is_dropbox_folder_url(url):
        return DropboxCheck(
            ok=False,
            message=(
                "Ese enlace no es una carpeta compartida de Dropbox "
                "(/scl/fo/ o /sh/). Pega el enlace de la carpeta, o un link directo al archivo."
            ),
        )
    try:
        token, scope, http, own = _open_scoped(settings, client)
    except MediaHostError as exc:
        return DropboxCheck(ok=False, message=scrub_secrets(str(exc)))
    try:
        scopes = {part for part in scope.split() if part}
        if scopes and "sharing.read" not in scopes:
            return DropboxCheck(ok=False, message=sharing_read_text())
        probe = http.post(
            _SHARED_LINK_METADATA_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"url": url},
        )
        body = probe.text or ""
        if _missing_sharing_read(probe.status_code, body):
            return DropboxCheck(ok=False, message=sharing_read_text())
        if probe.status_code >= 400:
            return DropboxCheck(
                ok=False,
                message=(
                    "No se pudo leer el enlace compartido "
                    f"({probe.status_code}). {scrub_secrets(_snippet(body))}"
                ).strip(),
            )
        files = _list_shared_files(http, token, url)
        names = tuple(str(item.get("name") or "") for item in files if item.get("name"))
        count = len(names)
        noun = "archivo" if count == 1 else "archivos"
        return DropboxCheck(
            ok=True,
            message=(
                f"OK: el token tiene sharing.read y la carpeta compartida tiene {count} {noun}."
            ),
            file_count=count,
            names=names,
        )
    except MediaHostError as exc:
        if exc.reason == SHARING_READ_REASON:
            return DropboxCheck(ok=False, message=sharing_read_text())
        return DropboxCheck(ok=False, message=scrub_secrets(str(exc)))
    finally:
        if own:
            http.close()


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


def pick_cover_image(entries: list[dict], *, video_name: str | None = None) -> dict | None:
    """Choose the still image that belongs with ``video_name``.

    Names match after folding non-breaking spaces and curly quotes. A Premiere
    ``.Still001.png`` shares a key with the video stem. Returns None when no
    image is a clear cover.
    """
    images = [entry for entry in entries if _is_file(entry) and _ext(entry) in _IMAGE_EXT]
    if not images:
        return None
    chosen_video = (video_name or "").strip() or _largest_video_name(
        [entry for entry in entries if _is_file(entry) and _ext(entry) in _VIDEO_EXT]
    )
    video_key = cover_match_key(chosen_video) if chosen_video else ""
    if video_key:
        exact = [
            entry
            for entry in images
            if cover_match_key(str(entry.get("name") or "")) == video_key
        ]
        if exact:
            return _best_cover(exact)
    stills = [entry for entry in images if "still" in str(entry.get("name") or "").casefold()]
    if len(stills) == 1:
        return stills[0]
    if len(images) == 1:
        return images[0]
    if stills:
        return _best_cover(stills)
    return None


def cover_match_key(name: str) -> str:
    """Comparable stem: NBSP and curly quotes folded, ``.Still001`` removed."""
    translated = (name or "").translate(_FOLD)
    stem = _STILL_SUFFIX.sub("", Path(translated).stem)
    return " ".join(stem.casefold().split())


def entry_in_account(entry: dict) -> bool:
    """True when Dropbox mounted the file in our account (``path_lower`` set)."""
    for key in ("path_lower", "path_display"):
        raw = entry.get(key)
        if isinstance(raw, str) and raw.startswith("/"):
            return True
    return False


def shared_relative_path(parent: str, name: str) -> str:
    """Path of ``name`` inside the shared folder, always starting with ``/``."""
    cleaned = name.replace("\\", "/").strip().strip("/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise MediaHostError(f"Dropbox shared file name is not usable ({name!r})")
    base = (parent or "").rstrip("/")
    if base and not base.startswith("/"):
        base = f"/{base}"
    suffix = "/".join(parts)
    return f"{base}/{suffix}" if base else f"/{suffix}"


def _best_cover(images: list[dict]) -> dict:
    return sorted(
        images,
        key=lambda entry: (
            0 if _ext(entry) == ".png" else 1,
            cover_match_key(str(entry.get("name") or "")),
        ),
    )[0]


def _largest_video_name(videos: list[dict]) -> str | None:
    if not videos:
        return None
    ordered = sorted(videos, key=lambda entry: int(entry.get("size") or 0), reverse=True)
    name = ordered[0].get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


def _list_shared_files(http: httpx.Client, token: str, folder_url: str) -> list[dict]:
    files, folders = _files_under(http, token, folder_url, "")
    if files:
        return files
    nested: list[dict] = []
    for folder in folders:
        name = str(folder.get("name") or "").strip()
        if not name:
            continue
        child_files, _deeper = _files_under(
            http,
            token,
            folder_url,
            shared_relative_path("", name),
        )
        nested.extend(child_files)
    return nested


def _files_under(
    http: httpx.Client,
    token: str,
    folder_url: str,
    parent: str,
) -> tuple[list[dict], list[dict]]:
    entries = _list_path(http, token, folder_url, path=parent)
    files: list[dict] = []
    folders: list[dict] = []
    for entry in entries:
        tag = str(entry.get(".tag") or "")
        if tag == "file":
            name = str(entry.get("name") or "").strip()
            item = dict(entry)
            if name:
                item["relative_path"] = shared_relative_path(parent, name)
            files.append(item)
        elif tag == "folder":
            folders.append(entry)
    return files, folders


def _open(
    settings: Settings,
    client: httpx.Client | None,
) -> tuple[str, httpx.Client, bool]:
    token, _scope, http, own = _open_scoped(settings, client)
    return token, http, own


def _open_scoped(
    settings: Settings,
    client: httpx.Client | None,
) -> tuple[str, str, httpx.Client, bool]:
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
        token, scope = _refresh(http, *creds)
    except Exception:
        if own:
            http.close()
        raise
    return token, scope, http, own


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


def _refresh(http: httpx.Client, refresh: str, app_key: str, app_secret: str) -> tuple[str, str]:
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
        snippet = scrub_secrets(_snippet(response.text or ""))
        raise MediaHostError(f"Dropbox token refresh failed ({response.status_code}): {snippet}")
    data = response.json() or {}
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise MediaHostError("Dropbox token refresh returned no access_token")
    scope = str(data.get("scope") or "").strip()
    return token, scope


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
            body = response.text or ""
            _raise_api(
                response.status_code,
                body,
                action="Dropbox files/list_folder",
                missing_path=path or "/",
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


def _stream_download(
    http: httpx.Client,
    token: str,
    url: str,
    arg: str,
    dest: Path,
    *,
    action: str,
    missing_path: str,
) -> None:
    try:
        with http.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Dropbox-API-Arg": arg,
            },
        ) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", errors="replace")
                _raise_api(
                    response.status_code,
                    body,
                    action=action,
                    missing_path=missing_path,
                )
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
    except MediaHostError:
        raise
    except httpx.HTTPError as exc:
        raise MediaHostError(f"{action} failed: {exc}") from exc
    if dest.stat().st_size == 0:
        raise MediaHostError(f"{action} returned an empty file")


def _raise_api(status: int, body: str, *, action: str, missing_path: str) -> None:
    if _missing_sharing_read(status, body):
        raise MediaHostError(sharing_read_text(), reason=SHARING_READ_REASON)
    if _file_not_found(status, body):
        raise MediaHostError(file_not_found_text(missing_path), reason=FILE_NOT_FOUND_REASON)
    snippet = scrub_secrets(_snippet(body))
    raise MediaHostError(f"{action} failed ({status}): {snippet}")


def _missing_sharing_read(status: int, body: str) -> bool:
    if status not in {401, 403}:
        return False
    folded = body.lower()
    return "missing_scope" in folded and "sharing.read" in folded


def _file_not_found(status: int, body: str) -> bool:
    if status != 409:
        return False
    return "not_found" in body.lower()


def _snippet(body: str) -> str:
    return " ".join(body.split())[:300]


def _api_arg(payload: dict) -> str:
    """ASCII JSON for the ``Dropbox-API-Arg`` header (non-ASCII as ``\\uXXXX``)."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _is_file(entry: dict) -> bool:
    return str(entry.get(".tag") or "") == "file"


def _ext(entry: dict) -> str:
    name = str(entry.get("name") or "")
    return Path(name).suffix.lower()
