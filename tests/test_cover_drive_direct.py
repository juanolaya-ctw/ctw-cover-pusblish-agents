from unittest.mock import MagicMock

import pytest

from metricool_sync_posts.cover.attach import resolve_miniatura_url

URL = 'https://drive.google.com/uc?export=download&id=130ls965KuP_cNA00ixuwwTGekOYIJkzW'


def test_public_drive_direct_dry_run():
    mc = MagicMock()
    assert resolve_miniatura_url(URL, metricool=mc, dry_run=True) == URL
    mc.normalize_media_url.assert_not_called()


def test_public_drive_direct_normalized():
    mc = MagicMock()
    mc.normalize_media_url.return_value = {'url': 'https://cdn.example/c.png'}
    assert resolve_miniatura_url(URL, metricool=mc, dry_run=False) == 'https://cdn.example/c.png'
    mc.normalize_media_url.assert_called_once_with(URL)


@pytest.mark.parametrize('url', [
    'https://drive.google.com/drive/folders/abc',
    'https://drive.google.com/file/d/abc/view',
    'https://drive.google.com/uc?export=view&id=abc',
    'https://drive.google.com/uc?export=download',
    'https://drive.google.com/uc?export=download&id=',
    'https://drive.google.com/uc?export=download&id=a&id=b',
    'https://drive.google.com/uc?export=download&id=abc&authuser=0',
    'https://drive.google.com:443/uc?export=download&id=abc',
    'https://drive.google.com/uc?export=download&id=abc#preview',
    'http://drive.google.com/uc?export=download&id=abc',
])
def test_other_drive_shapes_rejected(url):
    mc = MagicMock()
    assert resolve_miniatura_url(url, metricool=mc, dry_run=True) is None
    mc.normalize_media_url.assert_not_called()


def test_normalization_failure_remains_optional():
    mc = MagicMock()
    mc.normalize_media_url.side_effect = RuntimeError('unfetchable')
    assert resolve_miniatura_url(URL, metricool=mc, dry_run=False) is None
