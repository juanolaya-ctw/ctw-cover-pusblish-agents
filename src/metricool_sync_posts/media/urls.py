"""Detect Dropbox / Google Drive share URLs (no Dropbox OAuth / API)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def is_dropbox_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "dropbox.com" in host or "dropboxusercontent.com" in host


def is_dropbox_folder_url(url: str) -> bool:
    """True for folder shares (``/scl/fo/``, ``/sh/``). ``dl=1`` of those is a ZIP.

    ``/scl/fi/`` and ``/s/`` are files and are not treated as folders.
    """
    path = urlparse(url).path.lower()
    if "/scl/fi/" in path:
        return False
    if "/sh/" in path:
        return True
    return "/scl/fo/" in path


def is_google_drive_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "drive.google.com" in host or "docs.google.com" in host


def _dropbox_with_params(url: str, *, dl: str | None = None, raw: str | None = None) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    # Drop preview params that break direct download
    qs.pop("preview", None)
    if dl is not None:
        qs["dl"] = [dl]
    if raw is not None:
        qs["raw"] = [raw]
    # Keep first value per key; preserve rlkey and other share tokens
    flat = [(k, v[0]) for k, v in qs.items()]
    new_query = urlencode(flat)
    return urlunparse(parsed._replace(query=new_query))


def dropbox_direct_url(url: str) -> str:
    """Prefer dl=1 (+ raw=1) for direct download from a public/share link."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "dropbox.com" not in host and "dropboxusercontent.com" not in host:
        return url
    # Already on content CDN: ensure dl/raw
    if "dropboxusercontent.com" in host:
        return _dropbox_with_params(url, dl="1", raw="1")
    return _dropbox_with_params(url, dl="1", raw="1")


def dropbox_download_candidates(url: str) -> list[str]:
    """
    Ordered URL variants to try without Dropbox API/OAuth.

    Share links sometimes need dl=1, raw=1, or the dl.dropboxusercontent.com host.
    """
    if not is_dropbox_url(url):
        return [url]

    primary = dropbox_direct_url(url)
    candidates: list[str] = [primary]

    # dl=1 only (some older /s/ links dislike raw=1)
    dl_only = _dropbox_with_params(url, dl="1")
    if dl_only not in candidates:
        candidates.append(dl_only)

    parsed = urlparse(primary)
    host = parsed.netloc.lower()
    if "www.dropbox.com" in host or host == "dropbox.com":
        # Classic rewrite used by many downloaders
        rewritten = urlunparse(
            parsed._replace(netloc="dl.dropboxusercontent.com")
        )
        # content host often ignores dl; still set raw=1
        rewritten = _dropbox_with_params(rewritten, dl="1", raw="1")
        if rewritten not in candidates:
            candidates.append(rewritten)

    return candidates


def extract_drive_file_id(url: str) -> str | None:
    """Extract a Drive file id from /file/d/… or uc?id= / open?id= URLs."""
    if "/drive/folders/" in url or "/folders/" in url:
        return None
    if "/file/d/" in url:
        return url.split("/file/d/")[1].split("/")[0] or None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("id",):
        vals = qs.get(key)
        if vals and vals[0]:
            return vals[0]
    return None


def google_drive_direct_url(url: str) -> str:
    """
    Public uc?export=download link (fallback only when no service account).

    Prefer authenticated Drive API download via GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE.
    """
    if "/drive/folders/" in url or "/folders/" in url:
        raise ValueError(
            "Archivo Final is a Google Drive folder link; use a direct file URL (/file/d/…) "
            "or set GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE for folder resolution."
        )
    file_id = extract_drive_file_id(url)
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url
