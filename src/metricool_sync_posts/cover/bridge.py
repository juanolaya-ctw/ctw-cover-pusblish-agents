"""Lazy wrapper around ctw-cover-agent prepare_cover_before_metricool."""

from __future__ import annotations

import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.render import render_hook_cover
from metricool_sync_posts.notion.properties import NotionPostRow

logger = logging.getLogger(__name__)

# Names the cover agent has used for the optional Dropbox access token.
_TOKEN_PARAMS = ("dropbox_access_token", "dropbox_token", "access_token", "token")
# Read-only / no-upload flags. Absent means dry-run must not call the agent.
_DRY_PARAMS = ("dry_run", "read_only", "readonly", "no_upload")
# Functions the cover agent may expose to paint Titulo on a frame we already have.
_RENDER_NAMES = (
    "render_cover_from_frame",
    "render_cover_on_frame",
    "render_local_frame",
    "render_hook_on_frame",
    "render_cover",
    "compose_cover",
)
_FRAME_BYTE_PARAMS = ("frame_bytes", "still_bytes", "image_bytes", "local_frame")
_FRAME_PATH_PARAMS = (
    "frame_path",
    "still_path",
    "image_path",
    "local_frame_path",
)


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


def _load_cover_module(agent_root: str):
    """The cover module, or None when CTW_COVER_AGENT_PATH cannot be imported."""
    path_inserted = False
    if agent_root not in sys.path:
        sys.path.insert(0, agent_root)
        path_inserted = True
    try:
        import integrations.publish_agent.cover as cover_module
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
    return cover_module


def _load_prepare(agent_root: str):
    module = _load_cover_module(agent_root)
    if module is None:
        return None
    prepare = getattr(module, "prepare_cover_before_metricool", None)
    if not callable(prepare):
        logger.warning(
            "Cover agent at %s has no prepare_cover_before_metricool",
            agent_root,
        )
        return None
    return prepare


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


def _call_prepare(
    fn,
    task: dict[str, Any],
    token: str | None,
    *,
    dry_run: bool,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Pass only arguments the installed function accepts."""
    sig = _signature(fn)
    if sig is None:
        if token:
            return fn(task, token)
        return fn(task)

    params = list(sig.parameters.values())
    kwargs: dict[str, Any] = {}
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    supplied = extra or {}
    rest = params[1:]
    for param in rest:
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if param.name in supplied:
            kwargs[param.name] = supplied[param.name]
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
        _log_unready(
            row.page_id,
            "import_failed",
            "cover agent import failed or prepare_cover_before_metricool is missing",
        )
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
        failure = {
            "ready": False,
            "reason": "error",
            "message": f"{type(exc).__name__}: {exc}",
        }
        _log_unready(row.page_id, "error", failure["message"])
        return failure
    finally:
        if prev is None:
            os.environ.pop("CTW_COVER_AGENT_PATH", None)
        else:
            os.environ["CTW_COVER_AGENT_PATH"] = prev

    if not isinstance(result, dict):
        message = f"cover agent returned {type(result).__name__}"
        logger.warning("Cover agent returned non-dict for %s: %r", row.page_id, result)
        _log_unready(row.page_id, "error", message)
        return {"ready": False, "reason": "error", "message": message}
    if not result.get("ready") and not result.get("dry_run_skipped"):
        _log_unready(
            row.page_id,
            str(result.get("reason") or "").strip() or "error",
            str(result.get("message") or "").strip() or "-",
        )
    return result


def render_cover_on_local_frame(
    settings: Settings,
    *,
    titulo: str,
    frame_bytes: bytes,
    frame_path: Path,
    dry_run: bool = False,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Paint ``titulo`` on a still this process already downloaded.

    Prefers a cover-agent function that accepts the local frame (same styling
    as the Drive path). When the installed agent has no such entry point, uses
    :func:`render_hook_cover` — the local 4:5 renderer this repo calls with the
    same ``CTW_COVER_AGENT_PATH`` import. Never returns the untouched still.
    """
    text = (titulo or "").strip()
    agent_root = (settings.ctw_cover_agent_path or "").strip()
    if agent_root:
        rendered = _render_with_agent(
            agent_root,
            titulo=text,
            frame_bytes=frame_bytes,
            frame_path=frame_path,
            dry_run=dry_run,
            task=task,
            token=(settings.dropbox_access_token or "").strip() or None,
        )
        if rendered is not None:
            return rendered
        logger.info(
            "Cover agent has no local-frame renderer (CTW_COVER_AGENT_PATH=%s); "
            "using the local 4:5 hook renderer",
            agent_root,
        )
    return _render_locally(
        settings,
        titulo=text,
        frame_path=frame_path,
        agent_root=agent_root or None,
    )


def _log_unready(page_id: str, reason: str, message: str) -> None:
    logger.info(
        "prepare_cover_for_publish_task not ready page=%s reason=%s message=%s",
        page_id,
        reason,
        message,
    )


def _render_with_agent(
    agent_root: str,
    *,
    titulo: str,
    frame_bytes: bytes,
    frame_path: Path,
    dry_run: bool,
    task: dict[str, Any] | None,
    token: str | None,
) -> dict[str, Any] | None:
    """The agent result, or None when it cannot take a local frame."""
    module = _load_cover_module(agent_root)
    if module is None:
        return None
    renderer = _local_frame_renderer(module)
    if renderer is None:
        return None
    logger.info(
        "Rendering Titulo over a local frame with cover agent %s",
        getattr(renderer, "__name__", "renderer"),
    )
    payload = dict(task or {})
    payload.setdefault("titulo", titulo)
    payload["frame_path"] = str(frame_path)
    payload["frame_bytes"] = frame_bytes
    try:
        if getattr(renderer, "__name__", "") == "prepare_cover_before_metricool":
            result = _call_prepare(
                renderer,
                payload,
                token,
                dry_run=dry_run,
                extra=_frame_extra(renderer, frame_bytes, frame_path),
            )
        else:
            result = _call_frame_renderer(
                renderer,
                titulo=titulo,
                frame_bytes=frame_bytes,
                frame_path=frame_path,
                task=payload,
                token=token,
                dry_run=dry_run,
            )
    except Exception as exc:
        logger.exception("Cover agent local-frame render failed: %s", exc)
        return {
            "ready": False,
            "reason": "render_failed",
            "source": "dropbox",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return _normalize_render_result(result)


def _local_frame_renderer(module):
    """A cover-agent callable that accepts a local frame, or None."""
    for name in _RENDER_NAMES:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    prepare = getattr(module, "prepare_cover_before_metricool", None)
    if callable(prepare) and _frame_extra(prepare, b"", Path(".")):
        return prepare
    return None


def _frame_extra(fn, frame_bytes: bytes, frame_path: Path) -> dict[str, Any]:
    """Kwargs for a frame the function's signature actually accepts."""
    sig = _signature(fn)
    if sig is None:
        return {}
    names = set(sig.parameters)
    extra: dict[str, Any] = {}
    for name in _FRAME_BYTE_PARAMS:
        if name in names:
            extra[name] = frame_bytes
    if "frame" in names and getattr(fn, "__name__", "") in _RENDER_NAMES:
        extra["frame"] = frame_bytes
    for name in _FRAME_PATH_PARAMS:
        if name in names:
            extra[name] = str(frame_path)
    return extra


def _call_frame_renderer(
    fn,
    *,
    titulo: str,
    frame_bytes: bytes,
    frame_path: Path,
    task: dict[str, Any],
    token: str | None,
    dry_run: bool,
) -> Any:
    """Call a cover-agent renderer with the still bytes and the hook."""
    sig = _signature(fn)
    values: dict[str, Any] = {
        "frame_bytes": frame_bytes,
        "still_bytes": frame_bytes,
        "image_bytes": frame_bytes,
        "local_frame": frame_bytes,
        "frame": frame_bytes,
        "frame_path": str(frame_path),
        "still_path": str(frame_path),
        "image_path": str(frame_path),
        "local_frame_path": str(frame_path),
        "titulo": titulo,
        "title": titulo,
        "text": titulo,
        "hook": titulo,
        "cover_text": titulo,
        "task": task,
        "dry_run": dry_run,
        "read_only": dry_run,
        "readonly": dry_run,
        "no_upload": dry_run,
    }
    for name in _TOKEN_PARAMS:
        values[name] = token
    if sig is None:
        return fn(frame_bytes, titulo)

    params = [
        param
        for param in sig.parameters.values()
        if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    kwargs: dict[str, Any] = {}
    positional: list[Any] = []
    for param in params:
        if param.name not in values:
            if param.default is inspect.Parameter.empty and not positional:
                positional.append(frame_bytes)
            continue
        if param.name in _DRY_PARAMS and not dry_run:
            continue
        if (
            param.name in _TOKEN_PARAMS
            and not token
            and param.default is not inspect.Parameter.empty
        ):
            continue
        if param.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional.append(values[param.name])
            continue
        kwargs[param.name] = values[param.name]
    if positional and not kwargs:
        return fn(*positional)
    return fn(*positional, **kwargs)


def _normalize_render_result(result: Any) -> dict[str, Any]:
    if isinstance(result, (bytes, bytearray)) and result:
        return {"ready": True, "cover_bytes": bytes(result), "source": "dropbox"}
    if not isinstance(result, dict):
        return {
            "ready": False,
            "reason": "render_failed",
            "source": "dropbox",
            "message": f"cover renderer returned {type(result).__name__}",
        }
    normalized = dict(result)
    normalized.setdefault("source", "dropbox")
    if (
        normalized.get("ready")
        and not normalized.get("cover_bytes")
        and not normalized.get("cover_url")
    ):
        normalized["ready"] = False
        normalized.setdefault("reason", "render_failed")
        normalized.setdefault("message", "cover renderer returned no image")
    return normalized


def _render_locally(
    settings: Settings,
    *,
    titulo: str,
    frame_path: Path,
    agent_root: str | None,
) -> dict[str, Any]:
    dest = frame_path.with_name("cover-designed.png")
    try:
        png = render_hook_cover(
            ffmpeg_bin=settings.ffmpeg_bin,
            titulo=titulo,
            frame_path=frame_path,
            dest=dest,
            agent_root=agent_root,
        )
    except (OSError, RuntimeError) as exc:
        logger.warning("Local reel cover render failed: %s", exc)
        return {
            "ready": False,
            "reason": "render_failed",
            "source": "dropbox",
            "message": str(exc),
        }
    return {"ready": True, "cover_bytes": png, "source": "dropbox", "renderer": "local"}
