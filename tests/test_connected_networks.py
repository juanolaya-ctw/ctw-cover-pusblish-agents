"""Canal Ig and networks actually connected on the Colombia Tech brand."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.metricool.channels import (
    explicit_canal_network,
    normalize_channel,
    plan_canals,
)
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.profiles import (
    connected_networks_from_profile,
    load_connected_networks,
    publishable_networks,
)

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 9, 15, 0, tzinfo=BOG)
BLOG = "5822365"


def _settings(tmp_path, **extra):
    data = dict(
        NOTION_TOKEN="x",
        NOTION_DATABASE_ID="y",
        METRICOOL_USER_TOKEN="z",
        METRICOOL_USER_ID="1",
        METRICOOL_BLOG_ID=BLOG,
        ENABLE_SCHEDULE=True,
        TIMEZONE=TZ,
        MEDIA_WORK_DIR=tmp_path / "media",
        SLACK_DEDUPE_FILE=tmp_path / "slack.json",
        REQUIRE_COVER_FOR_SCHEDULE=False,
    )
    data.update(extra)
    return Settings(**data)


def _brand(**overrides):
    profile = {
        "id": 5822365,
        "label": "Colombia Tech",
        "instagram": "colombiatechoficial",
        "youtube": "ColombiaTech",
        "tiktok": "colombiatechweek",
        "linkedinCompany": "12345",
        "facebook": "",
        "twitter": None,
        "fbBusinessId": "178400000000",
    }
    profile.update(overrides)
    return profile


def _row(**overrides):
    base = dict(
        page_id="3f299829-d217-81cf-83ee-e66e8ef5139b",
        url="https://notion.so/row",
        channel=None,
        channels=["Canal Ig"],
        title="Pieza de feed",
        caption="texto del feed de colombiatechoficial para esta semana",
        final_file_url="https://cdn.example/video.mp4",
        content_type="Piezas estática",
        publication=datetime(2026, 10, 10, 15, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp_path, rows, *, profiles, dry=False):
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    notion.fetch_linked_in_window.return_value = []
    metricool.get_scheduled_posts.return_value = []
    metricool.create_scheduled_post.return_value = {"id": 99}
    metricool.get_simple_profiles.return_value = profiles
    with (
        patch("metricool_sync_posts.jobs.schedule.NotionRepository", return_value=notion),
        patch("metricool_sync_posts.jobs.schedule.MetricoolClient", return_value=metricool),
        patch("metricool_sync_posts.jobs.schedule.now_in", return_value=NOW),
        patch(
            "metricool_sync_posts.jobs.schedule.prepare_media_urls_for_metricool",
            return_value=["https://cdn.example/video.mp4"],
        ) as media,
        patch("metricool_sync_posts.jobs.schedule.notify_slack") as slack,
    ):
        stats = run_schedule(settings=settings, dry_run=dry)
    return stats, notion, metricool, media, slack


def test_canal_ig_is_instagram_by_exact_label():
    assert explicit_canal_network("Canal Ig") == "instagram"
    assert explicit_canal_network("CANAL IG") == "instagram"
    assert explicit_canal_network("Canal  Ig") == "instagram"
    assert explicit_canal_network("Canal de otra cosa") is None
    assert normalize_channel("Canal Ig") == "instagram"
    plan = plan_canals(["Canal Ig"])
    assert plan.networks == ("instagram",)
    assert plan.dropped == ()
    assert plan.skip_nico is False


def test_unconnected_labels_are_dropped_not_blocking():
    labels = [
        "Canal Ig",
        "Luma",
        "Whatsapp",
        "Comunidades",
        "Terceros",
        "Mailing",
        "Content hub- web",
        "GovTech web",
        "Newsletter",
        "LinkedIn",
        "Facebook",
    ]
    plan = plan_canals(labels)
    assert plan.networks == ("instagram",)
    assert plan.skip_nico is False
    assert "Luma" in plan.dropped
    assert "Whatsapp" in plan.dropped
    assert "Newsletter" in plan.dropped
    assert "LinkedIn" in plan.dropped
    assert "Facebook" in plan.dropped
    assert "Content hub- web" in plan.dropped
    assert "GovTech web" in plan.dropped


def test_fb_business_id_is_not_a_facebook_page():
    connected = connected_networks_from_profile(_brand())
    assert connected == {"instagram", "youtube", "tiktok", "linkedin"}
    assert "facebook" not in connected
    assert publishable_networks(connected) == {"instagram", "youtube", "tiktok"}

    with_page = connected_networks_from_profile(_brand(facebook="Colombia Tech"))
    assert "facebook" in with_page


def test_simple_profiles_request_selects_this_blog(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {"id": 7255578, "instagram": "nico"},
                _brand(),
            ],
        )

    client = MetricoolClient(_settings(tmp_path))
    client._http.close()
    client._http = httpx.Client(
        base_url="https://app.metricool.com/api",
        transport=httpx.MockTransport(handler),
    )
    profiles = client.get_simple_profiles()
    client.close()
    assert seen["path"].endswith("/admin/simpleProfiles")
    assert seen["params"]["blogId"] == BLOG
    assert "from" not in seen["params"]
    networks = load_connected_networks(SimpleNamespace(
        get_simple_profiles=lambda: profiles,
    ), BLOG)
    assert networks == frozenset({"instagram", "tiktok", "youtube"})


def test_simple_profiles_failure_uses_instagram_tiktok_youtube():
    class Boom:
        def get_simple_profiles(self):
            raise RuntimeError("down")

    assert load_connected_networks(Boom(), BLOG) == frozenset(
        {"instagram", "tiktok", "youtube"}
    )


def test_canal_ig_row_schedules_instagram_and_logs_dropped(tmp_path, caplog):
    row = _row(
        channels=[
            "Canal Ig",
            "Luma",
            "Whatsapp",
            "Comunidades",
            "Terceros",
            "Mailing",
            "Content hub- web",
            "GovTech web",
            "Newsletter",
            "LinkedIn",
        ]
    )
    with caplog.at_level("INFO"):
        stats, notion, metricool, _media, slack = _run(tmp_path, [row], profiles=[_brand()])
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "instagram"}]
    notion.set_status.assert_called_once()
    assert "no_connected_network" not in [call.kwargs["reason"] for call in slack.call_args_list]
    dropped = next(line for line in caplog.text.splitlines() if "Dropped Canal" in line)
    for label in (
        "Luma",
        "Whatsapp",
        "Comunidades",
        "Terceros",
        "Mailing",
        "Newsletter",
        "LinkedIn",
    ):
        assert label in dropped
    assert "Content hub- web" in dropped
    assert "GovTech web" in dropped


def test_row_with_only_unconnected_canals_is_skipped(tmp_path, caplog):
    row = _row(
        channels=["Luma", "Whatsapp", "Comunidades", "Terceros", "Mailing", "GovTech web"]
    )
    with caplog.at_level("INFO"):
        stats, notion, metricool, media, slack = _run(tmp_path, [row], profiles=[_brand()])
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args.kwargs["reason"] == "no_connected_network"
    assert "Dropped Canal" in caplog.text


def test_ig_nico_still_skips_the_whole_row(tmp_path):
    row = _row(channels=["IG Nico", "Canal Ig", "TikTok"])
    stats, notion, metricool, media, slack = _run(tmp_path, [row], profiles=[_brand()])
    assert stats["queried"] == 0
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    media.assert_not_called()
    notion.set_status.assert_not_called()
    assert slack.call_args is None
    assert plan_canals(["IG Nico", "Canal Ig"]).skip_nico is True


def test_network_missing_on_the_brand_is_dropped(tmp_path, caplog):
    profile = _brand(tiktok="")
    row = _row(channels=["Canal Ig", "TikTok"])
    with caplog.at_level("INFO"):
        stats, _notion, metricool, _media, _slack = _run(tmp_path, [row], profiles=[profile])
    assert stats["scheduled"] == 1
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["providers"] == [{"network": "instagram"}]
    assert "TikTok" in caplog.text
