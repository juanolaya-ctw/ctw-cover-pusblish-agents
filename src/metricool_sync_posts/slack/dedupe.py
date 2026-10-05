"""Persist Slack alert dedupe state (same piece+reason within N hours)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DedupeStore:
    path: Path
    ttl_seconds: int

    def _load(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return {}

    def _save(self, data: dict[str, float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def should_notify(self, key: str) -> bool:
        now = time.time()
        data = self._load()
        expired = {k for k, ts in data.items() if now - ts > self.ttl_seconds}
        for k in expired:
            data.pop(k, None)
        last = data.get(key)
        if last is not None and now - last <= self.ttl_seconds:
            self._save(data)
            return False
        data[key] = now
        self._save(data)
        return True


def alert_key(notion_page_id: str, reason: str) -> str:
    return f"{notion_page_id}:{reason}"
