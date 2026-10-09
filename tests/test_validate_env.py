"""Unit tests for validate_env placeholder detection."""

from __future__ import annotations

import os
from unittest.mock import patch

from metricool_sync_posts.cli import validate_env


def test_placeholder_notion_token_fails() -> None:
    env = {
        "NOTION_TOKEN": "secret_xxx",
        "NOTION_DATABASE_ID": "29f99829-d217-805d-abc0-000ba0486e43",
        "METRICOOL_USER_TOKEN": "real-token",
        "METRICOOL_USER_ID": "12345",
    }
    with patch.dict(os.environ, env, clear=False):
        from metricool_sync_posts.config import load_settings

        settings = load_settings()
        assert validate_env._check_placeholders(settings) is False


def test_realistic_tokens_pass_placeholder_check() -> None:
    env = {
        "NOTION_TOKEN": "ntn_abc123secret",
        "NOTION_DATABASE_ID": "29f99829-d217-805d-abc0-000ba0486e43",
        "METRICOOL_USER_TOKEN": "mc-auth-value",
        "METRICOOL_USER_ID": "391827",
    }
    with patch.dict(os.environ, env, clear=False):
        from metricool_sync_posts.config import load_settings

        settings = load_settings()
        assert validate_env._check_placeholders(settings) is True
