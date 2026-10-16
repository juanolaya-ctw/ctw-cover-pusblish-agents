"""Persist Slack alert dedupe state.

An alert (same piece + reason) is sent again only when its text changed or
after ``ttl_seconds``. Entries also remember which reasons a page was skipped
for, so an expiry alert can name the original blocker.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path


def fingerprint(message: str) -> str:
    return hashlib.sha1((message or "").encode("utf-8")).hexdigest()[:12]


@dataclass
class DedupeStore:
    path: Path
    ttl_seconds: int
    # Reasons are remembered this long for the expiry alert, even after the TTL.
    history_seconds: int = 14 * 86400

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        data: dict[str, dict] = {}
        for key, value in raw.items():
            try:
                if isinstance(value, dict):
                    data[str(key)] = {
                        "ts": float(value.get("ts") or 0),
                        "fp": str(value.get("fp") or ""),
                    }
                else:
                    # Legacy format: {key: timestamp}.
                    data[str(key)] = {"ts": float(value), "fp": ""}
            except (TypeError, ValueError):
                continue
        return data

    def _save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def should_notify(self, key: str, message: str | None = None) -> bool:
        now = time.time()
        data = self._load()
        for stale in [k for k, v in data.items() if now - v["ts"] > self.history_seconds]:
            data.pop(stale, None)
        fp = fingerprint(message) if message is not None else ""
        last = data.get(key)
        if last is not None and now - last["ts"] <= self.ttl_seconds:
            changed = bool(fp) and bool(last["fp"]) and fp != last["fp"]
            if not changed:
                self._save(data)
                return False
        data[key] = {"ts": now, "fp": fp}
        self._save(data)
        return True

    def reasons_for(self, notion_page_id: str) -> list[str]:
        """Reasons previously alerted for this page (oldest first)."""
        prefix = f"{notion_page_id}:"
        items = [
            (value["ts"], key[len(prefix):])
            for key, value in self._load().items()
            if key.startswith(prefix)
        ]
        return [reason for _ts, reason in sorted(items)]


def alert_key(notion_page_id: str, reason: str) -> str:
    return f"{notion_page_id}:{reason}"
