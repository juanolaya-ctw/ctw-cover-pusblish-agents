"""Errors raised when media cannot be hosted durably."""

from __future__ import annotations


class MediaHostError(RuntimeError):
    """Raised when a post must be skipped instead of using a short-lived URL."""

    def __init__(self, message: str, *, reason: str = "media_host_failed") -> None:
        super().__init__(message)
        self.reason = reason
