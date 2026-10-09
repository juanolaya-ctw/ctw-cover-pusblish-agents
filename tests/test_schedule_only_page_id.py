from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import run_schedule

TARGET = '3e999829-d217-81f0-adad-dcd75070ff19'
OTHER = '3f299829-d217-814e-a5ec-d856e45ce5e4'
NOW = datetime(2026, 10, 9, 10, tzinfo=ZoneInfo('America/Bogota'))


def row(page_id, channel='Instagram'):
    return SimpleNamespace(page_id=page_id, channel=channel, title='fixture',
                           publication=NOW.replace(hour=16), caption='fixture',
                           url='https://notion.so/fixture', final_file_url='https://cdn.test/v.mp4',
                           content_type='Reel', miniatura_url=None)


def exercise(tmp_path, rows, target=TARGET, **kwargs):
    settings = Settings(NOTION_TOKEN='x', NOTION_DATABASE_ID='x', METRICOOL_USER_TOKEN='x',
                        METRICOOL_USER_ID='x', ENABLE_SCHEDULE=True,
                        MEDIA_WORK_DIR=tmp_path / 'media', SLACK_DEDUPE_FILE=tmp_path / 'slack')
    notion, mc = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    with (
        patch('metricool_sync_posts.jobs.schedule.NotionRepository', return_value=notion),
        patch('metricool_sync_posts.jobs.schedule.MetricoolClient', return_value=mc),
        patch('metricool_sync_posts.jobs.schedule.now_in', return_value=NOW),
        patch('metricool_sync_posts.jobs.schedule.caption_for_row', return_value='fixture'),
        patch('metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool',
              return_value=['https://cdn.test/v.mp4']) as media,
        patch('metricool_sync_posts.jobs.schedule.notify_slack'),
    ):
        try:
            stats = run_schedule(settings=settings, dry_run=True, only_page_id=target, **kwargs)
        except ValueError:
            media.assert_not_called()
            mc.create_scheduled_post.assert_not_called()
            notion.set_status.assert_not_called()
            raise
    return stats, notion, mc, media


def test_exact_target_beyond_normal_limit(tmp_path):
    rows = [row(OTHER)] * 6 + [row(TARGET)]
    stats, notion, mc, media = exercise(tmp_path, rows, target=TARGET.replace('-', ''))
    assert stats['scheduled'] == 1
    assert notion.fetch_approved_current_week.call_args.kwargs['limit'] is None
    assert media.call_count == 1
    mc.create_scheduled_post.assert_not_called()


@pytest.mark.parametrize('rows', [[], [row(OTHER)], [row(TARGET), row(TARGET)],
                                  [row(TARGET, 'LinkedIn Majo')]])
def test_zero_duplicate_or_excluded_fail_closed(tmp_path, rows):
    with pytest.raises(ValueError):
        exercise(tmp_path, rows)


def test_wrong_date_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        exercise(tmp_path, [row(TARGET)], only_publication_date=NOW.date().replace(day=8))


def test_invalid_uuid_before_clients(tmp_path):
    with pytest.raises(ValueError):
        exercise(tmp_path, [row(TARGET)], target='not-a-uuid')


def test_without_flag_unchanged_limit(tmp_path):
    stats, notion, _, media = exercise(tmp_path, [row(TARGET), row(OTHER)], target=None)
    assert stats['scheduled'] == 2
    assert notion.fetch_approved_current_week.call_args.kwargs['limit'] == 5
    assert media.call_count == 2
