"""TikTok auto-publish, manual-publish confirmation, and per-network slots."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from test_dryrun_fixes import BOG, _post, _programado, _run_schedule, _sched_row, _settings

from metricool_sync_posts.jobs.confirm_published import run_confirm_published
from metricool_sync_posts.jobs.content_types import build_schedule_body, tiktok_schedule_data
from metricool_sync_posts.metricool.matching import tiktok_manual_publish

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
WHEN = datetime(2026, 10, 12, 18, 0, tzinfo=BOG)
TESLA = (
    "Paul Moller llevo decadas detras de un auto volador y este video de Tesla "
    "cuenta por que esa idea nunca entro en la linea de produccion"
)
IG_FEED = (
    "El carrusel de la semana recorre las oficinas nuevas en Medellin y el equipo "
    "que llego el lunes pasado al edificio de la calle diez"
)
TIKTOK_LINK = "https://www.tiktok.com/@colombiatech/video/7234567890123456789"


def _pub():
    return datetime(2026, 10, 6, 12, 0, tzinfo=BOG)


def test_tiktok_video_and_image_always_send_autopublish_and_privacy():
    video = build_schedule_body(
        caption="video",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="TikTok",
        title="Tesla y Paul Moller",
        content_type="Reels",
        media_urls=["https://cdn.example/tesla.mp4"],
        networks=["tiktok"],
    )
    assert video["autoPublish"] is True
    assert video["tiktokData"] == tiktok_schedule_data(photo=False)
    assert video["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"
    assert video["tiktokData"]["disableComment"] is False
    assert video["tiktokData"]["disableDuet"] is False
    assert video["tiktokData"]["disableStitch"] is False
    assert "photoCoverIndex" not in video["tiktokData"]

    image = build_schedule_body(
        caption="fotos",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="TikTok",
        title="Carrusel",
        content_type="Carrusel",
        media_urls=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
        networks=["tiktok"],
    )
    assert image["autoPublish"] is True
    assert image["tiktokData"]["photoCoverIndex"] == 0
    assert image["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"

    instagram = build_schedule_body(
        caption="solo ig",
        publication=_pub(),
        tz_name="America/Bogota",
        channel="Instagram",
        title="Feed",
        content_type="Reels",
        media_urls=["https://cdn.example/reel.mp4"],
        networks=["instagram"],
    )
    assert "tiktokData" not in instagram
    assert "autoPublish" not in instagram


def test_published_without_tiktok_id_or_link_is_manual():
    manual = {"providers": [{"network": "tiktok", "status": "PUBLISHED"}]}
    assert tiktok_manual_publish(manual) is True
    empty_link = {
        "providers": [
            {"network": "tiktok", "status": "PUBLISHED", "publicUrl": "", "id": 0}
        ]
    }
    assert tiktok_manual_publish(empty_link) is True
    linked = {
        "providers": [
            {"network": "tiktok", "status": "PUBLISHED", "publicUrl": TIKTOK_LINK}
        ]
    }
    assert tiktok_manual_publish(linked) is False
    with_id = {
        "providers": [
            {"network": "tiktok", "status": "PUBLISHED", "id": "7234567890123456789"}
        ]
    }
    assert tiktok_manual_publish(with_id) is False
    pending = {"providers": [{"network": "tiktok", "status": "PENDING"}]}
    assert tiktok_manual_publish(pending) is False
    instagram = {"providers": [{"network": "instagram", "status": "PUBLISHED"}]}
    assert tiktok_manual_publish(instagram) is False


def _confirm(tmp_path, row, post):
    settings = _settings(tmp_path, SLACK_WEBHOOK_URL="https://hooks.slack.test/abc")
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_scheduled_in_window.return_value = [row]
    metricool.get_scheduled_posts.return_value = [post]
    with (
        patch(
            "metricool_sync_posts.jobs.confirm_published.NotionRepository",
            return_value=notion,
        ),
        patch(
            "metricool_sync_posts.jobs.confirm_published.MetricoolClient",
            return_value=metricool,
        ),
        patch("metricool_sync_posts.jobs.confirm_published.now_in", return_value=NOW),
        patch("metricool_sync_posts.jobs.confirm_published.notify_slack") as slack,
    ):
        stats = run_confirm_published(settings=settings, dry_run=False)
    return stats, notion, slack


def test_confirm_does_not_mark_manual_tiktok_published(tmp_path):
    text = "el video de tiktok se quedo en aviso manual y nunca salio a la red"
    row = _programado(
        "page-tt",
        text,
        datetime(2026, 10, 8, 12, tzinfo=BOG),
        channel="TikTok",
        title="Tesla y Paul Moller",
    )
    post = _post(
        post_id=70,
        text=text,
        when="2026-10-08T12:00:00",
        providers=[{"network": "tiktok", "status": "PUBLISHED", "detailedStatus": "Published"}],
    )
    stats, notion, slack = _confirm(tmp_path, row, post)
    assert stats["published"] == 0
    assert stats["tiktok_manual"] == 1
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "tiktok_manual_publish"
    assert "TikTok quedó en publicación manual / no salió" in slack.call_args.kwargs["message"]


def test_confirm_marks_tiktok_when_the_post_has_a_link(tmp_path):
    text = "el video de tiktok si salio y metricool guardo el enlace publico"
    row = _programado(
        "page-tt-ok",
        text,
        datetime(2026, 10, 8, 12, tzinfo=BOG),
        channel="TikTok",
        title="Tesla y Paul Moller",
    )
    post = _post(
        post_id=71,
        text=text,
        when="2026-10-08T12:00:00",
        providers=[
            {
                "network": "tiktok",
                "status": "PUBLISHED",
                "publicUrl": TIKTOK_LINK,
            }
        ],
    )
    stats, notion, slack = _confirm(tmp_path, row, post)
    assert stats["published"] == 1
    notion.set_status.assert_called_once_with("page-tt-ok", "Publicado", dry_run=False)
    slack.assert_not_called()


def test_confirm_blocks_when_only_tiktok_lacks_a_link(tmp_path):
    text = "instagram salio pero tiktok se quedo en el aviso manual de metricool"
    row = _programado(
        "page-mix",
        text,
        datetime(2026, 10, 8, 12, tzinfo=BOG),
        channel="Instagram, TikTok",
        title="Tesla y Paul Moller",
    )
    post = _post(
        post_id=72,
        text=text,
        when="2026-10-08T12:00:00",
        providers=[
            {"network": "instagram", "status": "PUBLISHED", "publicUrl": "https://www.instagram.com/p/abc/"},
            {"network": "tiktok", "status": "PUBLISHED"},
        ],
    )
    stats, notion, slack = _confirm(tmp_path, row, post)
    assert stats["published"] == 0
    assert stats["tiktok_manual"] == 1
    notion.set_status.assert_not_called()
    assert "TikTok quedó en publicación manual / no salió" in slack.call_args.kwargs["message"]


def test_reconcile_manual_tiktok_stays_off_publicado(tmp_path):
    row = _sched_row(
        page_id="3e555555-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/paul-moller",
        channel="TikTok",
        channels=["TikTok"],
        title="Tesla y Paul Moller",
        caption=TESLA,
        content_type="Reels",
        publication=WHEN,
    )
    post = _post(
        post_id=80,
        text=TESLA,
        when="2026-10-12T18:00:00",
        providers=[{"network": "tiktok", "status": "PUBLISHED"}],
    )
    stats, notion, metricool, _media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[post]
    )
    assert stats["reconciled"] == 1
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)
    assert slack.call_args.kwargs["reason"] == "tiktok_manual_publish"
    assert "TikTok quedó en publicación manual / no salió" in slack.call_args.kwargs["message"]


def test_tesla_paul_moller_ig_feed_does_not_block_tiktok(tmp_path):
    """An Instagram feed post at the same minute must not block TikTok."""
    row = _sched_row(
        page_id="3e555555-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/paul-moller",
        channel="TikTok",
        channels=["TikTok"],
        title="Tesla y Paul Moller",
        caption=TESLA,
        content_type="Reels",
        publication=WHEN,
    )
    feed = _post(
        post_id=501,
        text=IG_FEED,
        when="2026-10-12T18:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        instagram_type="POST",
    )
    stats, notion, metricool, _media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[feed]
    )
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    assert stats["reconciled"] == 0
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "tiktok"}]
    assert body["autoPublish"] is True
    assert body["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"
    assert body["tiktokData"]["disableComment"] is False
    assert body["tiktokData"]["disableDuet"] is False
    assert body["tiktokData"]["disableStitch"] is False
    reasons = [call.kwargs["reason"] for call in slack.call_args_list]
    assert "slot_conflict" not in reasons
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)


def test_partial_clash_schedules_tiktok_and_alerts_only_instagram(tmp_path):
    row = _sched_row(
        page_id="3e666666-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/paul-moller-both",
        channel="Instagram, TikTok",
        channels=["Instagram", "TikTok"],
        title="Tesla y Paul Moller",
        caption=TESLA,
        content_type="Reels",
        cover_text="Hook del video",
        publication=WHEN,
    )
    feed = _post(
        post_id=502,
        text=IG_FEED,
        when="2026-10-12T18:05:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        instagram_type="REEL",
    )
    stats, notion, metricool, _media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[feed]
    )
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "tiktok"}]
    assert body["autoPublish"] is True
    assert body["tiktokData"]["privacyOption"] == "PUBLIC_TO_EVERYONE"
    assert "instagramData" not in body
    reasons = [call.kwargs["reason"] for call in slack.call_args_list]
    assert reasons.count("slot_conflict") == 1
    assert "scheduled" in reasons
    message = next(
        call.kwargs["message"]
        for call in slack.call_args_list
        if call.kwargs["reason"] == "slot_conflict"
    )
    assert "Instagram feed no se programó" in message
    assert "TikTok sí se programa" in message
    assert "Metricool 502" in message
    assert "Notion no se cambió" not in message
    notion.set_status.assert_called_once_with(row.page_id, "Programado", dry_run=False)


def test_peer_feed_does_not_block_the_tiktok_half(tmp_path):
    feed_row = _sched_row(
        page_id="3e111111-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/feed",
        channel="Instagram",
        channels=["Instagram"],
        title="Oficinas Medellin",
        caption=IG_FEED,
        content_type="Carrusel",
        publication=WHEN,
    )
    both = _sched_row(
        page_id="3e666666-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/paul-moller-both",
        channel="Instagram, TikTok",
        channels=["Instagram", "TikTok"],
        title="Tesla y Paul Moller",
        caption=TESLA,
        content_type="Reels",
        cover_text="Hook del video",
        publication=WHEN,
    )
    stats, _notion, metricool, _media, slack = _run_schedule(
        tmp_path, [feed_row, both], dry=False
    )
    assert stats["scheduled"] == 2
    assert stats["skipped"] == 0
    bodies = [call.args[0] for call in metricool.create_scheduled_post.call_args_list]
    assert [item["network"] for item in bodies[0]["providers"]] == ["instagram"]
    assert [item["network"] for item in bodies[1]["providers"]] == ["tiktok"]
    assert bodies[1]["autoPublish"] is True
    message = next(
        call.kwargs["message"]
        for call in slack.call_args_list
        if call.kwargs["reason"] == "slot_conflict"
    )
    assert "Instagram feed no se programó" in message
    assert "https://notion.so/feed" in message
    assert "TikTok sí se programa" in message


def test_every_clashing_network_still_skips_the_whole_row(tmp_path):
    row = _sched_row(
        page_id="3e666666-d217-81ce-a105-edddd431fb7d",
        url="https://notion.so/paul-moller-both",
        channel="Instagram, TikTok",
        channels=["Instagram", "TikTok"],
        title="Tesla y Paul Moller",
        caption=TESLA,
        content_type="Reels",
        publication=WHEN,
    )
    feed = _post(
        post_id=502,
        text=IG_FEED,
        when="2026-10-12T18:00:00",
        providers=[{"network": "instagram", "status": "PENDING"}],
        instagram_type="POST",
    )
    tiktok = _post(
        post_id=503,
        text="otro clip de tiktok que ya ocupa ese minuto y no es esta pieza",
        when="2026-10-12T18:10:00",
        providers=[{"network": "tiktok", "status": "PENDING"}],
    )
    stats, notion, metricool, media, slack = _run_schedule(
        tmp_path, [row], dry=False, posts=[feed, tiktok]
    )
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "slot_conflict"
    message = slack.call_args.kwargs["message"]
    assert "no se programó" in message
    assert "Instagram feed" in message
    assert "TikTok" in message
    assert "Notion no se cambió" in message
