from __future__ import annotations

import logging

import httpx

from metricool_sync_posts.slack.dedupe import DedupeStore, alert_key

logger = logging.getLogger(__name__)


def post_to_slack(*, webhook_url: str, channel: str, text: str, context: str) -> bool:
    payload = {"channel": channel, "text": text}
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Slack notify failed (%s): %s", context, exc)
        return False
    logger.info("Slack sent ok (%s) status=%s chars=%s", context, resp.status_code, len(text))
    return True


def notify_slack(
    *,
    webhook_url: str | None,
    channel: str,
    dedupe: DedupeStore,
    notion_page_id: str,
    reason: str,
    message: str,
    dry_run: bool,
    digest=None,
    title: str | None = None,
    url: str | None = None,
) -> None:
    """Send (or queue into ``digest``) one alert, deduped by page+reason+text."""
    if not webhook_url and digest is None:
        return
    if dry_run:
        # No webhook call and no dedupe file write.
        logger.info("[dry-run] Slack: %s", message)
        return
    key = alert_key(notion_page_id, reason)
    if not dedupe.should_notify(key, message):
        logger.info("Slack dedupe skip page=%s reason=%s", notion_page_id, reason)
        return
    if digest is not None:
        digest.add(
            notion_page_id=notion_page_id,
            reason=reason,
            message=message,
            title=title,
            url=url,
        )
        return
    post_to_slack(
        webhook_url=webhook_url,
        channel=channel,
        text=message,
        context=f"page={notion_page_id} reason={reason}",
    )
