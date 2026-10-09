from metricool_sync_posts.config import Settings


def _base(**overrides):
    env = {
        "NOTION_TOKEN": "n",
        "NOTION_DATABASE_ID": "d",
        "METRICOOL_USER_TOKEN": "m",
        "METRICOOL_USER_ID": "1",
    }
    env.update(overrides)
    return Settings(**env)


def test_require_cover_false_by_default_when_schedule_enabled():
    """Go-live: schedule must not block on covers / Dropbox API."""
    assert _base(ENABLE_SCHEDULE=True).require_cover_for_schedule is False


def test_require_cover_false_when_schedule_disabled():
    assert _base(ENABLE_SCHEDULE=False).require_cover_for_schedule is False


def test_require_cover_explicit_true_opt_in():
    s = _base(ENABLE_SCHEDULE=True, REQUIRE_COVER_FOR_SCHEDULE=True)
    assert s.require_cover_for_schedule is True


def test_require_cover_explicit_false():
    s = _base(ENABLE_SCHEDULE=True, REQUIRE_COVER_FOR_SCHEDULE=False)
    assert s.require_cover_for_schedule is False
