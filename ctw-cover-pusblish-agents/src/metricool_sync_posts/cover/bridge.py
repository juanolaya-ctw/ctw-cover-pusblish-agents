"""Lazy wrapper around ctw-cover-agent prepare_cover_before_metricool."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from metricool_sync_posts.config import Settings
from metricool_sync_posts.notion.properties import NotionPostRow

logger = logging.getLogger(__name__)


def publish_task_from_row(row: NotionPostRow) -> dict[str, Any]:
    """Shape expected by ctw-cover-agent integrations.publish_agent.cover."""
    return {
        "archivo_final_url": row.final_file_url or "",
        "titulo": row.title or "",
        "protagonistas": row.protagonistas,
        "miniatura_url": row.miniatura_url or "",
        "notion_page_id": row.page_id,
    }


def prepare_cover_for_publish_task(
    settings: Settings,
    row: NotionPostRow,
) -> dict[str, Any] | None:
    """
    Call ctw-cover-agent when CTW_COVER_AGENT_PATH is set.

    Returns the agent result dict (may include cover_bytes, status, message) or None
    when integration is disabled or unavailable.
    """
    agent_root = (settings.ctw_cover_agent_path or "").strip()
    if not agent_root:
        logger.debug("Cover agent skipped: CTW_COVER_AGENT_PATH not set")
        return None

    task = publish_task_from_row(row)
    dropbox_token = (settings.dropbox_access_token or "").strip()

    path_inserted = False
    if agent_root not in sys.path:
        sys.path.insert(0, agent_root)
        path_inserted = True

    try:
        from integrations.publish_agent.cover import prepare_cover_before_metricool
    except ImportError as exc:
        logger.warning(
            "Cover agent import failed (check CTW_COVER_AGENT_PATH=%s): %s",
            agent_root,
            exc,
        )
        return None
    finally:
        if path_inserted:
            try:
                sys.path.remove(agent_root)
            except ValueError:
                pass

    # Re-insert for the call only if the module was loaded from agent_root
    prev = os.environ.get("CTW_COVER_AGENT_PATH")
    os.environ["CTW_COVER_AGENT_PATH"] = agent_root
    try:
        result = prepare_cover_before_metricool(task, dropbox_token)
    except Exception as exc:
        logger.exception("prepare_cover_before_metricool failed for %s: %s", row.page_id, exc)
        return None
    finally:
        if prev is None:
            os.environ.pop("CTW_COVER_AGENT_PATH", None)
        else:
            os.environ["CTW_COVER_AGENT_PATH"] = prev

    if not isinstance(result, dict):
        logger.warning("Cover agent returned non-dict for %s: %r", row.page_id, result)
        return None
    return result
