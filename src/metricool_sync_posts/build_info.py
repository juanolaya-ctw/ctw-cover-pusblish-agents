"""Build identity for log proof that Juan's Windows tree is running this code."""

from __future__ import annotations

import os
import subprocess
from importlib import metadata
from pathlib import Path

from metricool_sync_posts import __version__

_PACKAGE_DIR = Path(__file__).resolve().parent
_BUILD_MARKER = _PACKAGE_DIR / "BUILD.txt"


def _read_marker() -> str | None:
    if _BUILD_MARKER.is_file():
        text = _BUILD_MARKER.read_text(encoding="utf-8").strip()
        if text:
            return text.splitlines()[0].strip()
    return None


def _git_sha() -> str | None:
    # _PACKAGE_DIR = …/src/metricool_sync_posts → parents[1] = repo root
    repo_root = _PACKAGE_DIR.parents[1]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        sha = out.strip()
        return sha or None
    except Exception:
        return None


def build_label() -> str:
    """
    Stable label for startup logs.

    Preference: METRICOOL_SYNC_BUILD env → packaged BUILD.txt → git SHA → package version.
    """
    env = (os.environ.get("METRICOOL_SYNC_BUILD") or "").strip()
    if env:
        return env
    marker = _read_marker()
    if marker:
        return marker
    sha = _git_sha()
    if sha:
        return f"{__version__}+git.{sha}"
    try:
        return metadata.version("metricool-sync-posts")
    except metadata.PackageNotFoundError:
        return __version__
