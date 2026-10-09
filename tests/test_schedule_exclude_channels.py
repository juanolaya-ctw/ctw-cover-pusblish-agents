from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import (
    _channel_is_excluded,
    _norm_channel,
    run_schedule,
)


def test_default_excludes_newsletter_and_ig_nico():
    default = Settings.model_fields["schedule_exclude_channels"].default
    assert "Newsletter" in default
    assert "IG Nico" in default
    s = Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
    )
    assert "Newsletter" in s.schedule_exclude_channels_set()
    assert "IG Nico" in s.schedule_exclude_channels_set()


def test_norm_channel_case_and_nbsp():
    assert _norm_channel("IG Nico") == _norm_channel("ig nico")
    assert _norm_channel("IG\u00a0Nico") == _norm_channel("IG Nico")


def test_channel_is_excluded_casefold():
    exclude = frozenset({"IG Nico", "Newsletter"})
    assert _channel_is_excluded("ig nico", exclude)
    assert _channel_is_excluded("Newsletter", exclude)
    assert not _channel_is_excluded("Instagram", exclude)


def _row(*, page_id: str, channel: str):
    return SimpleNamespace(
        page_id=page_id,
        url=f"https://notion.so/{page_id}",
        channel=channel,
        title=f"title-{page_id[:8]}",
        caption="caption",
        final_file_url="https://www.dropbox.com/s/x/file.mp4?dl=0",
        content_type="Reels",
        publication=datetime(2026, 10, 8, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
        miniatura_url=None,
        protagonistas="",
        status="Aprobado - Edición Final",
    )


def test_exclude_ig_nico_before_media_pipeline():
    """IG Nico rows must never reach prepare_media_urls_for_metricool."""
    rows = [
        _row(page_id="3e999829-d217-81f0-adad-dcd75070ff19", channel="IG Nico"),
        _row(page_id="3f299829-d217-814e-a5ec-d856e45ce5e4", channel="IG Nico"),
        _row(page_id="3e599829-d217-8100-a81e-d56405fda07f", channel="YouTube"),
        _row(page_id="3e399829-d217-80b0-9da7-ddbde184c267", channel="Instagram"),
        _row(page_id="3f199829-d217-816b-9594-df2ea9d49e0b", channel="Instagram"),
    ]
    settings = Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        ENABLE_SCHEDULE=True,
        DRY_RUN=True,
        TIMEZONE="America/Bogota",
    )

    notion = MagicMock()
    notion.fetch_approved_current_week.return_value = list(rows)
    metricool = MagicMock()
    future = datetime(2026, 10, 8, 20, 0, tzinfo=ZoneInfo("America/Bogota"))

    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://example.com/m.mp4"],
        ) as prep_media,
        patch(
            "metricool_sync_posts.jobs.schedule.caption_for_row",
            return_value="caption",
        ),
        patch(
            "metricool_sync_posts.jobs.schedule.publication_dt",
            side_effect=lambda r, _tz: future,
        ),
        patch(
            "metricool_sync_posts.jobs.schedule.now_in",
            return_value=datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack"),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_cover_for_publish_task",
            return_value=None,
        ),
        patch(
            "metricool_sync_posts.jobs.schedule.calendar_week_bounds",
            return_value=(
                datetime(2026, 10, 5, 0, 0, tzinfo=ZoneInfo("America/Bogota")),
                datetime(2026, 10, 11, 23, 59, tzinfo=ZoneInfo("America/Bogota")),
            ),
        ),
    ):
        stats = run_schedule(
            settings=settings,
            dry_run=True,
            exclude_channels=frozenset({"IG Nico", "Newsletter"}),
        )

    assert stats["queried"] == 3
    assert prep_media.call_count == 3
    assert stats["errors"] == 0
    assert stats["scheduled"] == 3
