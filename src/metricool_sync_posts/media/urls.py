"""Detect Dropbox / Google Drive share URLs."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urlunparse


def is_dropbox_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "dropbox.com" in host or "dropboxusercontent.com" in host


def is_google_drive_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "drive.google.com" in host or "docs.google.com" in host


def dropbox_direct_url(url: str) -> str:
    """Prefer dl=1 for direct download."""
    parsed = urlparse(url)
    if "dropbox.com" not in parsed.netloc.lower():
        return url
    qs = parse_qs(parsed.query)
    qs["dl"] = ["1"]
    new_query = "&".join(f"{k}={v[0]}" for k, v in qs.items())
    return urlunparse(parsed._replace(query=new_query))


def google_drive_direct_url(url: str) -> str:
    """Best-effort direct link for Drive file URLs."""
    if "/file/d/" in url:
        file_id = url.split("/file/d/")[1].split("/")[0]
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url
