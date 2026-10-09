"""Lazy wrapper around ctw-cover-agent prepare_cover_before_metricool."""

from __future__ import annotations

import inspect
import logging
import os
import sys
from typing import Any

from metricool_sync_posts.config import Settings
from metricool_sync_posts.notion.properties import NotionPostRow

logger = logging.getLogger(__name__)

# Names the cover agent has used for the optional Dropbox access token.
_TOKEN_PARAMS = ("dropbox_access_token", "dropbox_token", "access_token", "token")
# Read-only / no-upload flags. Absent means dry-run must not call the agent.
_DRY_PARAMS = ("dry_run", "read_only", "readonly", "no_upload")


def publish_task_from_row(row: NotionPostRow) -> dict[str, Any]:
    """Shape expected by ctw-cover-agent integrations.publish_agent.cover.

    ``titulo`` is the cover hook (Notion rich text ``Titulo``), not the
    publication title.
    """
    return {
        "archivo_final_url": row.final_file_url or "",
        "titulo": (getattr(row, "cover_text", "") or "").strip(),
        "protagonistas": row.protagonistas,
        "miniatura_url": row.miniatura_url or "",
        "notion_page_id": row.page_id,
    }


def _load_prepare(agent_root: str):
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
    return prepare_cover_before_metricool


def _signature(fn):
    try:
        return inspect.signature(fn)
    except (TypeError, ValueError):
        return None


def agent_supports_dry_run(fn) -> bool:
    """True when the agent accepts a read-only / no-upload flag."""
    sig = _signature(fn)
    if sig is None:
        return False
    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return any(name in params for name in _DRY_PARAMS)


def _call_prepare(fn, task: dict[str, Any], token: str | None, *, dry_run: bool) -> Any:
    """Pass only arguments the installed function accepts."""
    sig = _signature(fn)
    if sig is None:
        if token:
            return fn(task, token)
        return fn(task)

    params = list(sig.parameters.values())
    kwargs: dict[str, Any] = {}
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    rest = params[1:]
    for param in rest:
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.name in _DRY_PARAMS:
            if dry_run:
                kwargs[param.name] = True
            continue
        if param.name in _TOKEN_PARAMS or "dropbox" in param.name.lower():
            if token or param.default is inspect.Parameter.empty:
                kwargs[param.name] = token
            continue
        if param.default is inspect.Parameter.empty:
            kwargs[param.name] = None
    if dry_run and has_var_kw and not any(name in kwargs for name in _DRY_PARAMS):
        kwargs["dry_run"] = True
    return fn(task, **kwargs)


def prepare_cover_for_publish_task(
    settings: Settings,
    row: NotionPostRow,
    *,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """
    Call ctw-cover-agent when CTW_COVER_AGENT_PATH is set.

    Returns the agent result. When ``dry_run`` is set and the agent has no
    read-only flag, returns ``{"dry_run_skipped": True}`` and does not call it.
    Returns None when the integration is disabled or the import fails.
    """
    agent_root = (settings.ctw_cover_agent_path or "").strip()
    if not agent_root:
        logger.debug("Cover agent skipped: CTW_COVER_AGENT_PATH not set")
        return None

    task = publish_task_from_row(row)
    dropbox_token = (settings.dropbox_access_token or "").strip() or None
    prepare = _load_prepare(agent_root)
    if prepare is None:
        return None

    if dry_run and not agent_supports_dry_run(prepare):
        return {
            "ready": False,
            "dry_run_skipped": True,
            "reason": "dry_run",
            "message": "cover agent has no read-only mode",
        }

    prev = os.environ.get("CTW_COVER_AGENT_PATH")
    os.environ["CTW_COVER_AGENT_PATH"] = agent_root
    try:
        result = _call_prepare(prepare, task, dropbox_token, dry_run=dry_run)
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
