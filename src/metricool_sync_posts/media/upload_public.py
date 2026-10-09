"""Upload processed media to a temporary public host for Metricool normalize."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from metricool_sync_posts.config import Settings

logger = logging.getLogger(__name__)


def upload_public_url(settings: Settings, path: Path) -> str:
    if settings.s3_endpoint and settings.s3_bucket and settings.s3_access_key:
        return _upload_s3(settings, path)
    if settings.transfer_sh_enabled:
        return _upload_transfer_sh(settings, path)
    raise RuntimeError(
        "Media needs a public URL for Metricool; TRANSFER_SH off and S3 not configured. "
        "Enable TRANSFER_SH or set S3_* env vars."
    )


def _upload_transfer_sh(settings: Settings, path: Path) -> str:
    url = settings.transfer_sh_url.rstrip("/") + "/" + path.name
    data = path.read_bytes()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = httpx.put(url, content=data, timeout=300.0)
            resp.raise_for_status()
            link = resp.text.strip()
            logger.info("Uploaded to transfer.sh: %s", link)
            return link
        except Exception as exc:
            last_exc = exc
            logger.warning("transfer.sh attempt %s failed: %s", attempt + 1, exc)
    raise RuntimeError(f"transfer.sh upload failed after retries: {last_exc}") from last_exc


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
