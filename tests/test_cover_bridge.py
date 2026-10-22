from __future__ import annotations

import sys
from types import ModuleType

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.bridge import (
    prepare_cover_for_publish_task,
    publish_task_from_row,
)
from metricool_sync_posts.notion.properties import NotionPostRow


def _settings(**overrides) -> Settings:
    base = {
        "NOTION_TOKEN": "n",
        "NOTION_DATABASE_ID": "d",
        "METRICOOL_USER_TOKEN": "m",
        "METRICOOL_USER_ID": "1",
    }
    base.update(overrides)
    return Settings(**base)


def _row(**kwargs) -> NotionPostRow:
    defaults = {
        "page_id": "page-1",
        "url": "https://notion.so/page-1",
        "status": "Aprobado",
        "publication": None,
        "channel": "Instagram",
        "caption": "cap",
        "final_file_url": "https://example.com/v.mp4",
        "title": "Trials reel",
        "content_type": "Reel",
        "miniatura_url": "https://example.com/thumb.png",
        "protagonistas": "Alice, Bob",
    }
    defaults.update(kwargs)
    return NotionPostRow(**defaults)


def test_publish_task_uses_cover_text_not_publication_title():
    task = publish_task_from_row(
        _row(title="Titulo de la publicación largo", cover_text="Hook corto")
    )
    assert task["notion_page_id"] == "page-1"
    assert task["archivo_final_url"] == "https://example.com/v.mp4"
    assert task["titulo"] == "Hook corto"
    assert task["protagonistas"] == "Alice, Bob"
    assert task["miniatura_url"] == "https://example.com/thumb.png"


def test_bridge_skips_when_path_unset():
    settings = _settings()
    assert prepare_cover_for_publish_task(settings, _row()) is None


def test_bridge_handles_import_error(monkeypatch, tmp_path):
    agent_root = tmp_path / "fake-agent"
    agent_root.mkdir()
    settings = _settings(CTW_COVER_AGENT_PATH=str(agent_root))
    monkeypatch.syspath_prepend(str(agent_root))
    assert prepare_cover_for_publish_task(settings, _row()) is None


def test_bridge_calls_agent_when_installed(monkeypatch, tmp_path):
    agent_root = tmp_path / "agent"
    pkg = agent_root / "integrations" / "publish_agent"
    pkg.mkdir(parents=True)

    cover_mod = ModuleType("integrations.publish_agent.cover")

    def fake_prepare(task, token):
        assert task["titulo"] == "Hook corto"
        assert token == "dbx-token"
        return {"ready": True, "cover_bytes": b"png-bytes", "source": "dropbox"}

    cover_mod.prepare_cover_before_metricool = fake_prepare  # type: ignore[attr-defined]
    sys.modules["integrations"] = ModuleType("integrations")
    sys.modules["integrations.publish_agent"] = ModuleType("integrations.publish_agent")
    sys.modules["integrations.publish_agent.cover"] = cover_mod

    settings = _settings(
        CTW_COVER_AGENT_PATH=str(agent_root),
        DROPBOX_ACCESS_TOKEN="dbx-token",
    )
    result = prepare_cover_for_publish_task(settings, _row(cover_text="Hook corto"))
    assert result is not None
    assert result["cover_bytes"] == b"png-bytes"

    for name in (
        "integrations.publish_agent.cover",
        "integrations.publish_agent",
        "integrations",
    ):
        sys.modules.pop(name, None)


def _install_cover(fn):
    cover_mod = ModuleType("integrations.publish_agent.cover")
    cover_mod.prepare_cover_before_metricool = fn  # type: ignore[attr-defined]
    sys.modules["integrations"] = ModuleType("integrations")
    sys.modules["integrations.publish_agent"] = ModuleType("integrations.publish_agent")
    sys.modules["integrations.publish_agent.cover"] = cover_mod


def _clear_cover():
    for name in (
        "integrations.publish_agent.cover",
        "integrations.publish_agent",
        "integrations",
    ):
        sys.modules.pop(name, None)


def test_bridge_does_not_call_agent_without_dry_run_support(tmp_path):
    def fake_prepare(task, token=None):
        raise AssertionError("dry-run must not call an agent with no read-only flag")

    _install_cover(fake_prepare)
    try:
        settings = _settings(CTW_COVER_AGENT_PATH=str(tmp_path))
        result = prepare_cover_for_publish_task(
            settings, _row(cover_text="El hook"), dry_run=True
        )
    finally:
        _clear_cover()
    assert result is not None
    assert result["dry_run_skipped"] is True
    assert result["ready"] is False


def test_bridge_passes_dry_run_and_skips_unused_token(tmp_path):
    seen: dict = {}

    def fake_prepare(task, dropbox_access_token=None, *, dry_run=False):
        seen["titulo"] = task["titulo"]
        seen["token"] = dropbox_access_token
        seen["dry_run"] = dry_run
        return {"ready": True, "cover_bytes": b"png", "source": "drive"}

    _install_cover(fake_prepare)
    try:
        settings = _settings(CTW_COVER_AGENT_PATH=str(tmp_path))
        result = prepare_cover_for_publish_task(
            settings, _row(cover_text="El hook"), dry_run=True
        )
    finally:
        _clear_cover()
    assert seen == {"titulo": "El hook", "token": None, "dry_run": True}
    assert result is not None
    assert result["ready"] is True


def test_local_frame_uses_agent_renderer_bytes(tmp_path):
    from metricool_sync_posts.cover.bridge import render_cover_on_local_frame

    seen: dict = {}
    still = b"\x89PNG-still"
    designed = b"\x89PNG-designed"

    def render_cover_from_frame(frame_bytes, titulo):
        seen["frame"] = frame_bytes
        seen["titulo"] = titulo
        return designed

    _install_cover(lambda task, token=None: {"ready": False, "reason": "missing_frame"})
    sys.modules["integrations.publish_agent.cover"].render_cover_from_frame = (  # type: ignore[attr-defined]
        render_cover_from_frame
    )
    frame = tmp_path / "still.png"
    frame.write_bytes(still)
    try:
        result = render_cover_on_local_frame(
            _settings(CTW_COVER_AGENT_PATH=str(tmp_path)),
            titulo="7 días para pensar como founder día 7",
            frame_bytes=still,
            frame_path=frame,
        )
    finally:
        _clear_cover()
    assert seen == {
        "frame": still,
        "titulo": "7 días para pensar como founder día 7",
    }
    assert result["ready"] is True
    assert result["cover_bytes"] == designed
    assert result["cover_bytes"] != still


def test_prepare_with_frame_param_receives_the_still(tmp_path):
    from metricool_sync_posts.cover.bridge import render_cover_on_local_frame

    seen: dict = {}

    def prepare_cover_before_metricool(task, token=None, frame_bytes=None):
        seen["titulo"] = task["titulo"]
        seen["frame"] = frame_bytes
        seen["token"] = token
        return {"ready": True, "cover_bytes": b"png-from-agent", "source": "dropbox"}

    _install_cover(prepare_cover_before_metricool)
    frame = tmp_path / "still.png"
    frame.write_bytes(b"raw")
    try:
        result = render_cover_on_local_frame(
            _settings(CTW_COVER_AGENT_PATH=str(tmp_path), DROPBOX_ACCESS_TOKEN="dbx"),
            titulo="El hook",
            frame_bytes=b"raw-bytes",
            frame_path=frame,
            task={"titulo": "El hook", "archivo_final_url": "https://dropbox.com/scl/fo/x"},
        )
    finally:
        _clear_cover()
    assert seen["titulo"] == "El hook"
    assert seen["frame"] == b"raw-bytes"
    assert seen["token"] == "dbx"
    assert result["cover_bytes"] == b"png-from-agent"


def test_agent_without_frame_entry_uses_local_renderer(tmp_path, monkeypatch):
    from metricool_sync_posts.cover.bridge import render_cover_on_local_frame

    def prepare_cover_before_metricool(task, token=None):
        raise AssertionError("prepare must not be called again when it cannot take a frame")

    _install_cover(prepare_cover_before_metricool)
    frame = tmp_path / "still.png"
    frame.write_bytes(b"raw")

    def local(**kwargs):
        assert kwargs["titulo"] == "El hook"
        assert kwargs["frame_path"] == frame
        return b"\x89PNG-local-4x5"

    monkeypatch.setattr(
        "metricool_sync_posts.cover.bridge.render_hook_cover",
        local,
    )
    try:
        result = render_cover_on_local_frame(
            _settings(CTW_COVER_AGENT_PATH=str(tmp_path)),
            titulo="El hook",
            frame_bytes=b"raw",
            frame_path=frame,
        )
    finally:
        _clear_cover()
    assert result["ready"] is True
    assert result["renderer"] == "local"
    assert result["cover_bytes"] == b"\x89PNG-local-4x5"
