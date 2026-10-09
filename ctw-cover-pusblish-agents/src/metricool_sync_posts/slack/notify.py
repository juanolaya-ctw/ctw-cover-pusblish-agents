from __future__ import annotations

import logging

import httpx

from metricool_sync_posts.slack.dedupe import DedupeStore, alert_key

logger = logging.getLogger(__name__)


def notify_slack(
    *,
    webhook_url: str | None,
    channel: str,
    dedupe: DedupeStore,
    notion_page_id: str,
    reason: str,
    message: str,
    dry_run: bool,
) -> None:
    if not webhook_url:
        return
    key = alert_key(notion_page_id, reason)
    if not dedupe.should_notify(key):
        logger.info("Slack dedupe skip page=%s reason=%s", notion_page_id, reason)
        return
    payload = {"channel": channel, "text": message}
    if dry_run:
        logger.info("[dry-run] Slack: %s", message)
        return
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Slack notify failed: %s", exc)
