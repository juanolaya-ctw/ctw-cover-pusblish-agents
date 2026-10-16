"""Fixes from the Oct 16 2026 audit: matching, ids, expiry, alerts, window, digest."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from notion_client.errors import APIResponseError

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.metricool.matching import (
    find_best_match,
    find_duplicate_candidates,
    format_match_evidence,
    split_stored_refs,
)
from metricool_sync_posts.slack.dedupe import DedupeStore, alert_key
from metricool_sync_posts.slack.digest import SlackDigest
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import rolling_window_bounds

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 16, 13, 0, tzinfo=BOG)  # Friday
BOLSA = (
    "Democratizar la Bolsa: cómo invertir en acciones colombianas desde el celular "
    "sin comisionista y con montos pequeños."
)
SESGOS = (
    "¿Puede la IA discriminar al aprobar un crédito? Los modelos aprenden sesgos de "
    "los datos históricos de los bancos."
)


def _settings(tmp_path, **extra):
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID="5822365",
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        MEDIA_WORK_DIR=tmp_path / "media",
        SLACK_DEDUPE_FILE=tmp_path / "slack.json",
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    data.update(extra)
    return Settings(**data)


def _post(post_id, text, when, network="instagram", status="PENDING", **extra):
    post = {
        "id": post_id,
        "text": text,
        "publicationDate": {"dateTime": when, "timezone": TZ},
        "providers": [{"network": network, "status": status}],
    }
    post.update(extra)
    return post


def _row(**overrides):
    base = dict(
        page_id="3ea99829-d217-815a-b472-d02c195eb26c",
        url="https://notion.so/bolsa",
        channel="TikTok",
        channels=["TikTok"],
        title="Democratizar la Bolsa",
        cover_text="Democratizar la Bolsa",
        caption=BOLSA,
        final_file_url="https://cdn.example/bolsa.mp4",
        content_type="Clip",
        publication=datetime(2026, 10, 17, 12, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp_path, rows, *, posts=(), dry=False, now=NOW, create=None, **extra):
    settings = _settings(tmp_path, **extra)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = list(posts)
    metricool.create_scheduled_post.return_value = create or {"data": {"id": 99, "uuid": "u-99"}}
    metricool.get_simple_profiles.return_value = []
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=now),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, slack, settings


def _reasons(slack):
    return [call.kwargs["reason"] for call in slack.call_args_list]


# 1. Matching ---------------------------------------------------------------


def test_same_slot_unrelated_caption_is_not_a_match():
    other = _post(1, SESGOS, "2026-10-17T12:00:00", network="tiktok")
    hits = find_duplicate_candidates(
        [other],
        caption=BOLSA,
        title="Democratizar la Bolsa",
        network="tiktok",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 17, 12, 0, tzinfo=BOG),
    )
    assert hits == []
    evidence = format_match_evidence(
        other,
        caption=BOLSA,
        title="Democratizar la Bolsa",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 17, 12, 0, tzinfo=BOG),
        network="tiktok",
    )
    assert "reason=none" in evidence
    assert "text_score=" in evidence and "threshold=0.50" in evidence


def test_same_slot_with_lightly_edited_caption_still_matches():
    edited = BOLSA.replace("pequeños.", "pequeños. Link en bio")
    post = _post(2, edited, "2026-10-17T12:05:00", network="tiktok")
    hits = find_duplicate_candidates(
        [post],
        caption=BOLSA,
        title=None,
        network="tiktok",
        tz_name=TZ,
        notion_publication=datetime(2026, 10, 17, 12, 0, tzinfo=BOG),
    )
    assert [h["id"] for h in hits] == [2]


def test_confirm_time_match_needs_copy_when_one_side_has_text():
    post = _post(3, "Comenta PANEL y te mando el link del panel completo", "2026-10-15T12:00:00")
    pub = datetime(2026, 10, 15, 12, 0, tzinfo=BOG)
    assert (
        find_best_match("Emprender y formar una familia sí es posible", None, pub, [post], TZ)
        is None
    )
    empty_story = _post(4, "", "2026-10-15T12:00:00")
    assert find_best_match(".", None, pub, [empty_story], TZ) is not None


def test_stored_refs_accept_several_ids():
    assert split_stored_refs("395017545, 395017546") == {"395017545", "395017546"}
    assert split_stored_refs(None) == set()


def test_schedule_does_not_reconcile_unrelated_slot_post(tmp_path):
    other = _post(5, SESGOS, "2026-10-17T12:00:00", network="tiktok")
    stats, notion, metricool, slack, _ = _run(tmp_path, [_row()], posts=[other])
    assert stats["reconciled"] == 0
    notion.set_status.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    assert "slot_conflict" in _reasons(slack)


# 2. Metricool id ----------------------------------------------------------


def test_created_ids_are_stored_in_notion(tmp_path):
    stats, notion, metricool, slack, _ = _run(tmp_path, [_row()])
    assert stats["scheduled"] == 1
    notion.set_metricool_refs.assert_called_once()
    kwargs = notion.set_metricool_refs.call_args.kwargs
    assert kwargs["ids"] == ["99"]
    assert kwargs["uuids"] == ["u-99"]
    assert "scheduled" in _reasons(slack)


def test_store_refs_failure_does_not_break_the_run(tmp_path):
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = [_row()]
    notion.fetch_linked_in_window.return_value = []
    notion.set_metricool_refs.side_effect = RuntimeError("no property")
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.return_value = {"id": 7}
    metricool.get_simple_profiles.return_value = []
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ),
        patch("metricool_sync_posts.jobs.schedule.notify_slack"),
    ):
        stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 1
    assert stats["errors"] == 0


def test_notion_set_metricool_refs_tolerates_missing_property(tmp_path):
    import httpx

    from metricool_sync_posts.notion.client import NotionRepository

    repo = NotionRepository(_settings(tmp_path))
    repo._client = MagicMock()
    repo._client.pages.update.side_effect = APIResponseError(
        code="validation_error",
        status=400,
        message="Metricool ID is not a property that exists.",
        headers=httpx.Headers(),
        raw_body_text="{}",
    )
    assert repo.set_metricool_refs("p", ids=["1"], dry_run=False) is False
    repo._client.pages.update.side_effect = None
    assert repo.set_metricool_refs("p", ids=["1", "2"], dry_run=False) is True
    props = repo._client.pages.update.call_args.kwargs["properties"]
    assert props["Metricool ID"]["rich_text"][0]["text"]["content"] == "1, 2"


# 3. Expired pieces --------------------------------------------------------


def test_expired_piece_gets_clear_alert_with_previous_reason(tmp_path):
    settings = _settings(tmp_path)
    store = DedupeStore(path=settings.slack_dedupe_file, ttl_seconds=3600)
    row = _row(publication=datetime(2026, 10, 15, 12, 0, tzinfo=BOG))
    store.should_notify(alert_key(row.page_id, "missing_hook"), "x")
    stats, notion, metricool, slack, _ = _run(tmp_path, [row])
    assert stats["skipped"] == 1
    call = [c for c in slack.call_args_list if c.kwargs["reason"] == "past_publication_date"][0]
    message = call.kwargs["message"]
    assert "https://notion.so/bolsa" in message
    assert "ya pasó" in message
    assert "hora futura" in message
    assert '"Titulo"' in message
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_not_called()


def test_new_future_time_is_scheduled_normally(tmp_path):
    row = _row(publication=NOW + timedelta(hours=5))
    stats, _notion, metricool, _slack, _ = _run(tmp_path, [row])
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_called_once()


# 4. Skip alerts and stories ----------------------------------------------


def test_empty_caption_alerts(tmp_path):
    row = _row(caption="")
    with patch("metricool_sync_posts.jobs.schedule.caption_for_row", return_value=""):
        stats, _n, metricool, slack, _ = _run(tmp_path, [row])
    assert stats["skipped"] == 1
    assert "missing_caption" in _reasons(slack)
    metricool.create_scheduled_post.assert_not_called()


def test_story_without_caption_or_titulo_is_scheduled(tmp_path):
    row = _row(
        channel="Instagram",
        channels=["Instagram"],
        caption="",
        title="",
        cover_text="",
        content_type="Historias",
        final_file_url="https://cdn.example/story.jpg",
    )
    with (
        patch("metricool_sync_posts.jobs.schedule.caption_for_row", return_value=""),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/story.jpg"],
        ),
    ):
        settings = _settings(tmp_path)
        notion, metricool = MagicMock(), MagicMock()
        notion.fetch_approved_current_week.return_value = [row]
        notion.fetch_linked_in_window.return_value = []
        metricool.get_scheduled_posts.return_value = []
        metricool.create_scheduled_post.return_value = {"id": 5}
        metricool.get_simple_profiles.return_value = []
        with (
            patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
            patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
            patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
            patch("metricool_sync_posts.jobs.schedule.notify_slack"),
        ):
            stats = run_schedule(settings=settings, dry_run=False)
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["text"] == ""


def test_prior_attempt_alerts(tmp_path):
    with patch("metricool_sync_posts.jobs.schedule.ScheduleGuard") as guard_cls:
        guard_cls.return_value.contains.return_value = True
        stats, _n, metricool, slack, _ = _run(tmp_path, [_row()])
    assert stats["skipped"] == 1
    assert "prior_attempt" in _reasons(slack)
    metricool.create_scheduled_post.assert_not_called()


def test_missing_canal_alert_names_field_in_spanish(tmp_path):
    stats, _n, _m, slack, _ = _run(tmp_path, [_row(channel=None, channels=[])])
    call = [c for c in slack.call_args_list if c.kwargs["reason"] == "no_connected_network"][0]
    assert '"Canal"' in call.kwargs["message"]
    assert "https://notion.so/bolsa" in call.kwargs["message"]


# 5. Rolling window --------------------------------------------------------


def test_rolling_window_covers_next_ten_days():
    start, end = rolling_window_bounds(NOW, TZ, back_days=7, ahead_days=10)
    assert start == datetime(2026, 10, 9, 0, 0, tzinfo=BOG)
    assert end.date() == datetime(2026, 10, 26).date()


def test_schedule_fetch_uses_lookahead(tmp_path):
    stats, notion, *_ = _run(tmp_path, [])
    start, end = notion.fetch_approved_current_week.call_args.args[:2]
    assert end.date() == (NOW + timedelta(days=10)).date()
    assert start.date() == (NOW - timedelta(days=7)).date()


# 6. Slack digest and dedupe ----------------------------------------------


def test_digest_sends_one_message_with_only_new_or_changed(tmp_path):
    store = DedupeStore(path=tmp_path / "d.json", ttl_seconds=86400)
    digest = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    common = dict(
        webhook_url="https://hooks.slack.test/x",
        channel="#c",
        dedupe=store,
        dry_run=False,
        digest=digest,
    )
    with patch("metricool_sync_posts.slack.notify.httpx.post") as post:
        notify_slack(notion_page_id="a", reason="missing_hook", message="falta Titulo", **common)
        notify_slack(notion_page_id="b", reason="missing_time", message="sin hora", **common)
        notify_slack(notion_page_id="a", reason="missing_hook", message="falta Titulo", **common)
        notify_slack(notion_page_id="c", reason="scheduled", message="programada", **common)
        post.assert_not_called()
        post.return_value.status_code = 200
        assert digest.flush() is True
        assert post.call_count == 1
        text = post.call_args.kwargs["json"]["text"]
    assert text.count("falta Titulo") == 1
    assert "sin hora" in text and "programada" in text
    assert "Necesitan atención (2)" in text

    digest2 = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    common["digest"] = digest2
    notify_slack(notion_page_id="a", reason="missing_hook", message="falta Titulo", **common)
    notify_slack(notion_page_id="b", reason="missing_time", message="sin hora (cambió)", **common)
    assert [i["page"] for i in digest2.items] == ["b"]


def test_empty_digest_sends_nothing():
    digest = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    with patch("metricool_sync_posts.slack.notify.httpx.post") as post:
        assert digest.flush() is False
    post.assert_not_called()


def test_successful_slack_send_is_logged(caplog):
    digest = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    digest.add(notion_page_id="a", reason="missing_time", message="sin hora")
    with patch("metricool_sync_posts.slack.notify.httpx.post") as post:
        post.return_value.status_code = 200
        with caplog.at_level("INFO"):
            digest.flush()
    assert "Slack sent ok" in caplog.text


def test_legacy_dedupe_file_is_read(tmp_path):
    path = tmp_path / "old.json"
    import json
    import time

    path.write_text(json.dumps({"p:missing_time": time.time()}))
    store = DedupeStore(path=path, ttl_seconds=3600)
    assert store.should_notify("p:missing_time", "x") is False
    assert store.reasons_for("p") == ["missing_time"]


# 7. Existing rules -------------------------------------------------------


def test_ig_nico_and_linkedin_still_excluded(tmp_path):
    nico = _row(
        page_id="3ea99829-d217-815a-b472-d02c195eb26d", channel="IG Nico", channels=["IG Nico"]
    )
    linkedin = _row(
        page_id="3ea99829-d217-815a-b472-d02c195eb26e", channel="LinkedIn", channels=["LinkedIn"]
    )
    stats, _n, metricool, _s, _ = _run(tmp_path, [nico, linkedin])
    metricool.create_scheduled_post.assert_not_called()
    assert stats["scheduled"] == 0
