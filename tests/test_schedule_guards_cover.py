from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.attach import resolve_miniatura_url
from metricool_sync_posts.jobs.content_types import build_schedule_body
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard, exact_existing_post
from metricool_sync_posts.metricool.channels import normalize_channel

PUB = datetime(2026, 10, 9, 16, tzinfo=ZoneInfo('America/Bogota'))
NOW = PUB.replace(hour=10)


@pytest.mark.parametrize(('channel', 'network'), [
    ('IG CTW', 'instagram'), ('YT', 'youtube'), ('TikTok', 'tiktok'),
    ('LinkedIn', 'linkedin'), ('IG/YT', None), ('???', None), (None, None),
])
def test_channel_network(channel, network):
    assert normalize_channel(channel) == network


@pytest.mark.parametrize('channel', ['YouTube', 'TikTok'])
def test_cover_does_not_leak(channel):
    body = build_schedule_body(
        caption='x', publication=PUB, tz_name='America/Bogota', channel=channel,
        title='x', content_type='Video', media_urls=['https://cdn.example/v.mp4'],
        cover_url='https://cdn.example/c.jpg',
    )
    assert 'videoThumbnailUrl' not in body
    assert 'instagramData' not in body


def test_unknown_payload_rejected():
    with pytest.raises(ValueError):
        build_schedule_body(caption='x', publication=PUB, tz_name='America/Bogota',
                            channel='???', title='x', content_type='Video')


def test_linkedin_payload_rejected():
    with pytest.raises(ValueError, match='LinkedIn'):
        build_schedule_body(caption='x', publication=PUB, tz_name='America/Bogota',
                            channel='LinkedIn', title='x', content_type='Post')


def test_miniatura_public_without_oauth():
    client = MagicMock()
    assert resolve_miniatura_url(
        'https://www.dropbox.com/s/x/c.jpg?dl=0', metricool=client, dry_run=True
    ) == 'https://www.dropbox.com/s/x/c.jpg?dl=1&raw=1'
    client.normalize_media_url.assert_not_called()
    for url in ['http://localhost/x', 'https://drive.google.com/file/d/x',
                'https://cdn.example/x?X-Amz-Expires=3600']:
        assert resolve_miniatura_url(url, metricool=client, dry_run=True) is None


def test_guard_persistent_atomic_and_brand_scoped(tmp_path):
    path = tmp_path / 'guard.sqlite3'
    one = ScheduleGuard(path)
    assert one.reserve('5822365', 'page')
    two = ScheduleGuard(path)
    assert two.contains('5822365', 'page')
    assert not two.reserve('5822365', 'page')
    assert two.reserve('other', 'page')


def test_exact_match_not_prefix_or_wrong_network():
    post = {'text': 'caption', 'providers': [{'network': 'instagram'}],
            'publicationDate': {'dateTime': PUB.isoformat(), 'timezone': 'America/Bogota'}}
    assert exact_existing_post([post], 'Caption', 'instagram', PUB, 'America/Bogota')
    assert not exact_existing_post([post], 'caption extra', 'instagram', PUB, 'America/Bogota')
    assert not exact_existing_post([post], 'caption', 'youtube', PUB, 'America/Bogota')


def exercise(tmp_path, *, dry, notion_failure=False, rows=None):
    settings = Settings(
        NOTION_TOKEN='fixture', NOTION_DATABASE_ID='fixture', METRICOOL_USER_TOKEN='fixture',
        METRICOOL_USER_ID='fixture', ENABLE_SCHEDULE=True, MEDIA_WORK_DIR=tmp_path / 'media',
        SLACK_DEDUPE_FILE=tmp_path / 'slack.json',
    )
    row = SimpleNamespace(page_id='fixture-ig', url='https://notion.so/fixture',
                          status='Aprobado - Edición Final', publication=PUB,
                          channel='IG CTW', caption='fixture caption', title='fixture Reel',
                          content_type='Reel', final_file_url='https://cdn.example/video.mp4',
                          miniatura_url='https://cdn.example/cover.jpg', protagonistas='',
                          cover_text='Hook de la portada')
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows or [row]
    if notion_failure:
        notion.set_status.side_effect = RuntimeError('Notion failure after POST')
    metricool.get_scheduled_posts.return_value = []
    metricool.normalize_media_url.return_value = {'url': row.miniatura_url}
    with (
        patch('metricool_sync_posts.jobs.schedule.NotionRepository', return_value=notion),
        patch('metricool_sync_posts.jobs.schedule.MetricoolClient', return_value=metricool),
        patch('metricool_sync_posts.jobs.schedule.now_in', return_value=NOW),
        patch('metricool_sync_posts.jobs.schedule.caption_for_row', return_value=row.caption),
        patch('metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool',
              return_value=[row.final_file_url]) as media,
        patch('metricool_sync_posts.jobs.schedule.build_schedule_body',
              wraps=build_schedule_body) as builder,
        patch('metricool_sync_posts.jobs.schedule.notify_slack'),
        patch(
            'metricool_sync_posts.cover.attach.fetch_https_bytes',
            return_value=b'\xff\xd8\xffok',
        ),
        patch(
            'metricool_sync_posts.cover.attach.host_cover_jpeg',
            return_value='https://static.metricool.com/video/1/202610/cover.jpg',
        ),
    ):
        stats = run_schedule(settings=settings, dry_run=dry,
                             exclude_channels=frozenset({'IG Nico'}))
    return stats, metricool, notion, builder, media


def test_schedule_dry_run_ig_miniatura(tmp_path):
    stats, mc, notion, builder, _ = exercise(tmp_path, dry=True)
    assert stats == {
        'queried': 1, 'scheduled': 1, 'skipped': 0, 'errors': 0, 'reconciled': 0,
    }
    assert builder.call_args.kwargs['cover_url'] == 'https://cdn.example/cover.jpg'
    mc.create_scheduled_post.assert_not_called()
    mc.normalize_media_url.assert_not_called()
    notion.set_status.assert_not_called()
    assert not (tmp_path / 'schedule-guard.sqlite3').exists()


def test_notion_failure_does_not_resend(tmp_path):
    stats, mc, _, _, _ = exercise(tmp_path, dry=False, notion_failure=True)
    assert stats['errors'] == 1
    mc.create_scheduled_post.assert_called_once()
    stats, mc, _, _, media = exercise(tmp_path, dry=False)
    assert stats['skipped'] == 1
    mc.create_scheduled_post.assert_not_called()
    media.assert_not_called()


def test_manual_and_unknown_channels_before_media(tmp_path):
    rows = [SimpleNamespace(page_id=str(i), channel=ch, title='x', publication=PUB)
            for i, ch in enumerate(['Newsletter', 'LinkedIn Majo', 'IG Nico', 'Unknown'])]
    stats, mc, _, _, media = exercise(tmp_path, dry=True, rows=rows)
    assert stats['scheduled'] == 0
    media.assert_not_called()
    mc.create_scheduled_post.assert_not_called()


def test_multi_channel_row_is_not_silently_first():
    from metricool_sync_posts.notion.properties import _first_channel, read_channel_names

    props = {'Canal': {'type': 'multi_select', 'multi_select': [
        {'name': 'Instagram'}, {'name': 'YouTube'},
    ]}}
    assert read_channel_names(props, 'Canal') == ['Instagram', 'YouTube']
    assert _first_channel(props, 'Canal') == 'Instagram, YouTube'


def test_still_photo_never_gets_video_thumbnail():
    body = build_schedule_body(caption='x', publication=PUB, tz_name='America/Bogota',
                               channel='Instagram', title='x', content_type='Foto',
                               cover_url='https://cdn.example/c.jpg')
    assert 'videoThumbnailUrl' not in body


def test_post_timeout_leaves_reservation(tmp_path):
    from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard

    guard = ScheduleGuard(tmp_path / 'schedule-guard.sqlite3')
    assert guard.reserve('5822365', 'timeout-page')
    # An unknown POST outcome must be reconciled, not automatically retried.
    assert not ScheduleGuard(guard.path).reserve('5822365', 'timeout-page')
