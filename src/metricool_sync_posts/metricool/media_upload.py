"""Upload bytes into Metricool planner storage.

Verified live on 2026-10-10 against ``PUT/PATCH /v2/media/s3/upload-transactions``.
The URL to store on a scheduled post is ``convertedFileUrl``
(``https://static.metricool.com/...``). Presigned S3 PUTs must not send
``X-Mc-Auth``; that header is only for the Metricool API calls.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from urllib.parse import urlparse

import httpx

from metricool_sync_posts.media.errors import MediaHostError

logger = logging.getLogger(__name__)

# Multipart chunk. S3 requires every part except the last to be at least 5 MB.
PART_SIZE = 8 * 1024 * 1024
# One part below this size is a SIMPLE upload. At or above it, split into parts.
SIMPLE_MAX = 5 * 1024 * 1024 - 1

_UPLOAD_PATH = "/v2/media/s3/upload-transactions"


def is_metricool_static_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "static.metricool.com"


def plan_ranges(
    size: int,
    *,
    part_size: int = PART_SIZE,
    simple_max: int = SIMPLE_MAX,
) -> list[tuple[int, int]]:
    """Byte ranges covering ``size``. End is exclusive.

    Files up to ``simple_max`` (5 MB minus 1 byte) are one part. Larger files
    are split on ``part_size`` (8 MB); only the last part may be shorter than 5 MB.
    """
    if size <= 0:
        raise MediaHostError("Refusing to upload an empty media file")
    if part_size < 1:
        raise MediaHostError("Upload part size must be positive")
    if size <= simple_max:
        return [(0, size)]
    return [(start, min(start + part_size, size)) for start in range(0, size, part_size)]


def sha256_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def upload_planner_bytes(
    api: httpx.Client,
    data: bytes,
    *,
    params: dict[str, str],
    content_type: str,
    file_extension: str,
    s3: httpx.Client | None = None,
    part_size: int = PART_SIZE,
    simple_max: int = SIMPLE_MAX,
) -> str:
    """Create a planner upload, PUT the bytes, complete, and return the public URL.

    ``api`` is the authenticated Metricool client (``X-Mc-Auth``). ``s3`` must
    not send that header. When ``s3`` is omitted a clean client is used.
    """
    if not data:
        raise MediaHostError("Refusing to upload an empty media file")
    extension = file_extension.lstrip(".").lower()
    if not extension or extension == "bin":
        raise MediaHostError(
            f"Refusing to upload .{extension or 'bin'}; file type was not detected"
        )
    ranges = plan_ranges(len(data), part_size=part_size, simple_max=simple_max)
    parts = [
        {
            "size": end - start,
            "startByte": start,
            "endByte": end,
            "hash": sha256_b64(data[start:end]),
        }
        for start, end in ranges
    ]
    body = {
        "resourceType": "planner",
        "contentType": content_type,
        "fileExtension": extension,
        "parts": parts,
    }
    created = _request_json(api, "PUT", body, params, step="create upload")
    own_s3 = s3 is None
    s3_client = s3 or httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0))
    try:
        complete_body = _put_presigned(s3_client, data, created, content_type)
    finally:
        if own_s3:
            s3_client.close()
    completed = _request_json(api, "PATCH", complete_body, params, step="complete upload")
    return _public_url(completed)


def _request_json(
    api: httpx.Client,
    method: str,
    body: dict,
    params: dict[str, str],
    *,
    step: str,
) -> dict:
    try:
        response = api.request(method, _UPLOAD_PATH, params=params, json=body)
    except httpx.HTTPError as exc:
        raise MediaHostError(f"Metricool {step} failed: {exc}") from exc
    if response.status_code >= 300:
        snippet = " ".join((response.text or "").split())[:500]
        raise MediaHostError(
            f"Metricool {step} returned {response.status_code}: {snippet or 'no body'}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise MediaHostError(f"Metricool {step} returned a non-JSON body") from exc
    return _unwrap(payload, step=step)


def _unwrap(payload: object, *, step: str) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload, dict) and (
        "uploadType" in payload or "fileUrl" in payload or "convertedFileUrl" in payload
    ):
        return payload
    raise MediaHostError(f"Metricool {step} response had no data object")


def _put_presigned(s3: httpx.Client, data: bytes, created: dict, content_type: str) -> dict:
    upload_type = str(created.get("uploadType") or "").upper()
    if not upload_type:
        if created.get("presignedUrl") and not created.get("parts"):
            upload_type = "SIMPLE"
        else:
            upload_type = "MULTIPART"
    if upload_type == "SIMPLE":
        presigned = str(created.get("presignedUrl") or "").strip()
        if not presigned:
            raise MediaHostError("Metricool simple upload response had no presignedUrl")
        file_url = str(created.get("fileUrl") or "").strip() or presigned.split("?", 1)[0]
        digest = sha256_b64(data)
        _s3_put(
            s3,
            presigned,
            data,
            headers={
                "Content-Type": content_type,
                "x-amz-checksum-sha256": digest,
            },
            label="simple",
        )
        return {"simple": {"fileUrl": file_url}}

    upload_id = str(created.get("uploadId") or "").strip()
    key = str(created.get("key") or "").strip()
    raw_parts = created.get("parts")
    if not upload_id or not key or not isinstance(raw_parts, list) or not raw_parts:
        raise MediaHostError(
            "Metricool multipart upload response was missing uploadId, key, or parts"
        )
    done: list[dict] = []
    for part in raw_parts:
        if not isinstance(part, dict):
            raise MediaHostError("Metricool multipart part was not an object")
        start = int(part["startByte"])
        end = int(part["endByte"])
        chunk = data[start:end]
        response = _s3_put(
            s3,
            str(part["presignedUrl"]),
            chunk,
            headers={"x-amz-checksum-sha256": sha256_b64(chunk)},
            label=f"part {part.get('partNumber')}",
        )
        etag = response.headers.get("etag") or response.headers.get("ETag")
        if not etag:
            raise MediaHostError(
                f"S3 part {part.get('partNumber')} response had no ETag"
            )
        done.append({"partNumber": part["partNumber"], "etag": etag})
    return {"multipart": {"uploadId": upload_id, "key": key, "parts": done}}


def _s3_put(
    s3: httpx.Client,
    url: str,
    body: bytes,
    *,
    headers: dict[str, str],
    label: str,
) -> httpx.Response:
    try:
        response = s3.put(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise MediaHostError(f"S3 {label} upload failed: {exc}") from exc
    if response.status_code >= 300:
        snippet = " ".join((response.text or "").split())[:300]
        raise MediaHostError(f"S3 {label} PUT returned {response.status_code}: {snippet}")
    if any(key.lower() == "x-mc-auth" for key in response.request.headers):
        raise MediaHostError("S3 upload sent X-Mc-Auth; the presigned URL would be rejected")
    return response


def _public_url(completed: dict) -> str:
    converted = str(completed.get("convertedFileUrl") or "").strip()
    file_url = str(completed.get("fileUrl") or "").strip()
    chosen = converted or file_url
    if not chosen:
        raise MediaHostError("Metricool complete upload response had no file URL")
    if not is_metricool_static_url(chosen):
        logger.warning(
            "Metricool upload URL is not on static.metricool.com (UI will not render it): %s",
            chosen[:160],
        )
    else:
        logger.info("Metricool media URL %s", chosen)
    return chosen
