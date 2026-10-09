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


def test_publish_task_from_row_shape():
    task = publish_task_from_row(_row())
    assert task["notion_page_id"] == "page-1"
    assert task["archivo_final_url"] == "https://example.com/v.mp4"
    assert task["titulo"] == "Trials reel"
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
        assert task["titulo"] == "Trials reel"
        assert token == "dbx-token"
        return {"cover_bytes": b"png-bytes", "status": "ok"}

    cover_mod.prepare_cover_before_metricool = fake_prepare  # type: ignore[attr-defined]
    sys.modules["integrations"] = ModuleType("integrations")
    sys.modules["integrations.publish_agent"] = ModuleType("integrations.publish_agent")
    sys.modules["integrations.publish_agent.cover"] = cover_mod

    settings = _settings(
        CTW_COVER_AGENT_PATH=str(agent_root),
        DROPBOX_ACCESS_TOKEN="dbx-token",
    )
    result = prepare_cover_for_publish_task(settings, _row())
    assert result is not None
    assert result["cover_bytes"] == b"png-bytes"

    for name in (
        "integrations.publish_agent.cover",
        "integrations.publish_agent",
        "integrations",
    ):
        sys.modules.pop(name, None)
