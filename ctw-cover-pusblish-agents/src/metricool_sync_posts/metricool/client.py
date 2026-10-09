from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.timeutil import iso_metricool

logger = logging.getLogger(__name__)


class MetricoolClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._http = httpx.Client(
            base_url=settings.metricool_base_url.rstrip("/"),
            headers={
                "X-Mc-Auth": settings.metricool_user_token,
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    def _params(self) -> dict[str, str]:
        return {
            "userId": self._s.metricool_user_id,
            "blogId": self._s.metricool_blog_id,
        }

    def get_scheduled_posts(
        self,
        from_dt: datetime,
        to_dt: datetime,
        *,
        extended_range: bool = True,
    ) -> list[dict[str, Any]]:
        params = {
            **self._params(),
            "from": iso_metricool(from_dt),
            "to": iso_metricool(to_dt),
            "timezone": self._s.metricool_timezone,
        }
        if extended_range:
            params["extendedRange"] = "true"
        resp = self._http.get("/v2/scheduler/posts", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "posts", "items", "results"):
                if isinstance(data.get(key), list):
                    return data[key]
        logger.warning("Unexpected get_scheduled_posts shape: %s", type(data))
        return []

    def normalize_media_url(self, url: str) -> dict[str, Any]:
        resp = self._http.get(
            "/actions/normalize/image/url",
            params={"url": url, **self._params()},
        )
        resp.raise_for_status()
        text = (resp.text or "").strip()
        if not text:
            return {}
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def create_scheduled_post(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post("/v2/scheduler/posts", params=self._params(), json=body)
        resp.raise_for_status()
        return resp.json()

    def update_scheduled_post(
        self,
        post_id: str,
        body: dict[str, Any],
        *,
        uuid: str | None = None,
    ) -> dict[str, Any]:
        params = self._params()
        if uuid:
            params["uuid"] = uuid
        resp = self._http.put(f"/v2/scheduler/posts/{post_id}", params=params, json=body)
        if resp.status_code == 405:
            resp = self._http.patch(f"/v2/scheduler/posts/{post_id}", params=params, json=body)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._http.close()
