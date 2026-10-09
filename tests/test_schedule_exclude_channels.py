from metricool_sync_posts.config import Settings


def test_default_excludes_newsletter():
    s = Settings(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
    )
    assert "Newsletter" in s.schedule_exclude_channels_set()
