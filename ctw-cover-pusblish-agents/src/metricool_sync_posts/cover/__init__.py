"""Optional integration with ctw-cover-agent (prepare cover before Metricool)."""

from metricool_sync_posts.cover.attach import resolve_cover_url_for_metricool
from metricool_sync_posts.cover.bridge import prepare_cover_for_publish_task

__all__ = ["prepare_cover_for_publish_task", "resolve_cover_url_for_metricool"]
