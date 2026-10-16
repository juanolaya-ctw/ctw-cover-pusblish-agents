"""One Slack message per run with only new or changed issues."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from metricool_sync_posts.slack.notify import post_to_slack

logger = logging.getLogger(__name__)

_OK_REASONS = frozenset({"scheduled"})
# Slack rejects very long messages; keep a safe margin.
_MAX_CHARS = 35000


@dataclass
class SlackDigest:
    webhook_url: str | None
    channel: str
    header: str
    items: list[dict] = field(default_factory=list)

    def add(
        self,
        *,
        notion_page_id: str,
        reason: str,
        message: str,
        title: str | None = None,
        url: str | None = None,
    ) -> None:
        self.items.append(
            {
                "page": notion_page_id,
                "reason": reason,
                "message": message,
                "title": title,
                "url": url,
            }
        )

    def render(self) -> str:
        problems = [item for item in self.items if item["reason"] not in _OK_REASONS]
        done = [item for item in self.items if item["reason"] in _OK_REASONS]
        lines = [self.header]
        if problems:
            lines.append("")
            lines.append(f":warning: *Necesitan atención ({len(problems)})*")
            lines.extend(f"• {item['message']}" for item in problems)
        if done:
            lines.append("")
            lines.append(f":white_check_mark: *Programadas en esta corrida ({len(done)})*")
            lines.extend(f"• {item['message']}" for item in done)
        text = "\n".join(lines)
        if len(text) > _MAX_CHARS:
            text = text[: _MAX_CHARS - 40] + "\n… (mensaje recortado)"
        return text

    def flush(self) -> bool:
        if not self.items:
            logger.info("Slack digest: nothing new this run")
            return False
        if not self.webhook_url:
            logger.info("Slack digest: %s items but no webhook configured", len(self.items))
            return False
        return post_to_slack(
            webhook_url=self.webhook_url,
            channel=self.channel,
            text=self.render(),
            context=f"digest items={len(self.items)}",
        )
