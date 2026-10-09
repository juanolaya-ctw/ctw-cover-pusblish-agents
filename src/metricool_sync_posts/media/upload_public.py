"""Upload processed media to a temporary public host for Metricool normalize."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from metricool_sync_posts.config import Settings

logger = logging.getLogger(__name__)

# Hosts verified reachable from the CTW cloud agent (2026-10-08):
#   litterbox.catbox.moe — OK (direct https://litter.catbox.moe/… bytes)
#   uguu.se — OK (direct https://h.uguu.se/… bytes)
# transfer.sh / 0x0.st timed out from agent + Juan's Windows; optional only.
# tmpfiles.org uploaded but returned HTML landing pages (not usable for Metricool).


def _litterbox_time(min_hours: int) -> str:
    """litterbox accepts 1h, 12h, 24h, or 72h."""
    if min_hours >= 72:
        return "72h"
    if min_hours >= 24:
        return "24h"
    if min_hours >= 12:
        return "12h"
    return "1h"


def upload_public_url(
    settings: Settings,
    path: Path,
    *,
    min_hours: int = 24,
    allow_short: bool = True,
) -> str:
    """
    Return a publicly fetchable HTTP(S) URL for ``path``.

    Order:
      1. S3 when S3_* is configured (durable)
      2. transfer.sh only when TRANSFER_SH / TRANSFER_SH_ENABLED (optional; may timeout)
      3. litterbox.catbox.moe (24h, or 72h when ``min_hours`` >= 72)
      4. uguu.se, unless ``allow_short`` is false and a 72h host was required
    """
    errors: list[str] = []

    if settings.s3_endpoint and settings.s3_bucket and settings.s3_access_key:
        try:
            return _upload_s3(settings, path)
        except Exception as exc:
            msg = f"s3: {exc}"
            logger.warning("Public upload failed (%s)", msg)
            errors.append(msg)

    if settings.transfer_sh_enabled:
        try:
            return _upload_transfer_sh(settings, path)
        except Exception as exc:
            msg = f"transfer.sh: {exc}"
            logger.warning("Public upload failed (%s); trying free hosts", msg)
            errors.append(msg)

    litter_time = _litterbox_time(min_hours)
    try:
        url = _upload_litterbox(path, time=litter_time)
        logger.info("Uploaded via litterbox (%s): %s", litter_time, url)
        return url
    except Exception as exc:
        msg = f"litterbox: {exc}"
        logger.warning("Public upload failed (%s)", msg)
        errors.append(msg)

    if min_hours >= 72 and not allow_short:
        raise RuntimeError(
            "No public host lasting at least 72h (S3 / litterbox). "
            f"Last errors: {errors}"
        )

    try:
        url = _upload_uguu(path)
        logger.info("Uploaded via uguu.se: %s", url)
        if min_hours >= 72:
            logger.warning(
                "Cover host uguu.se may expire before %sh: %s",
                min_hours,
                url,
            )
        return url
    except Exception as exc:
        msg = f"uguu.se: {exc}"
        logger.warning("Public upload failed (%s)", msg)
        errors.append(msg)

    raise RuntimeError(
        "All public upload hosts failed (S3 / transfer.sh / litterbox / uguu). "
        f"Last errors: {errors}. Set S3_* for a durable host, or check network egress."
    )


def _upload_transfer_sh(settings: Settings, path: Path) -> str:
    url = settings.transfer_sh_url.rstrip("/") + "/" + path.name
    last_exc: Exception | None = None
    # Short connect timeout: Juan's PC gets WinError 10060; fail fast to fallbacks.
    timeout = httpx.Timeout(connect=12.0, read=120.0, write=120.0, pool=12.0)
    for attempt in range(2):
        try:
            with path.open("rb") as f:
                resp = httpx.put(
                    url,
                    content=f,
                    headers={"Content-Length": str(path.stat().st_size)},
                    timeout=timeout,
                )
            resp.raise_for_status()
            link = resp.text.strip()
            if not link.startswith("http"):
                raise RuntimeError(f"transfer.sh returned non-URL body: {link[:120]!r}")
            logger.info("Uploaded to transfer.sh: %s", link)
            return link
        except Exception as exc:
            last_exc = exc
            logger.warning("transfer.sh attempt %s failed: %s", attempt + 1, exc)
    raise RuntimeError(f"transfer.sh upload failed after retries: {last_exc}") from last_exc


def _upload_litterbox(path: Path, *, time: str = "24h") -> str:
    """Anonymous upload; ``time`` is 1h, 12h, 24h, or 72h. Direct litter.catbox.moe URL."""
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=300.0, pool=20.0)
    with path.open("rb") as f:
        resp = httpx.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": time},
            files={"fileToUpload": (path.name, f, "application/octet-stream")},
            timeout=timeout,
        )
    resp.raise_for_status()
    link = resp.text.strip()
    if not link.startswith("http"):
        raise RuntimeError(f"litterbox returned non-URL body: {link[:120]!r}")
    return link


def _upload_uguu(path: Path) -> str:
    """Anonymous upload; returns direct https://h.uguu.se/… URL."""
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=300.0, pool=20.0)
    with path.open("rb") as f:
        resp = httpx.post(
            "https://uguu.se/upload.php",
            files={"files[]": (path.name, f, "application/octet-stream")},
            timeout=timeout,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"uguu.se upload unsuccessful: {resp.text[:160]!r}")
    files = data.get("files") or []
    if not files or not files[0].get("url"):
        raise RuntimeError(f"uguu.se missing file url: {resp.text[:160]!r}")
    return str(files[0]["url"])


def _upload_s3(settings: Settings, path: Path) -> str:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Install boto3 for S3 uploads: pip install boto3") from exc

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    key = path.name
    client.upload_file(str(path), settings.s3_bucket, key, ExtraArgs={"ACL": "public-read"})
    if settings.s3_public_base_url:
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"
    return f"{settings.s3_endpoint.rstrip('/')}/{settings.s3_bucket}/{key}"
