"""Page-body Título/Caption fill, expiry recovery, and the digest fix line."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
from notion_client.errors import APIResponseError

from metricool_sync_posts.config import Settings
from metricool_sync_posts.jobs.schedule import run_schedule
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard
from metricool_sync_posts.notion.body_copy import parse_body_copy
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.digest import SlackDigest

TZ = "America/Bogota"
BOG = ZoneInfo(TZ)
NOW = datetime(2026, 10, 16, 13, 0, tzinfo=BOG)
BLOG = "5822365"
PAGE = "3ea99829-d217-815a-b472-d02c195eb26c"

GEN_Z_TITLE = "LA GENERACIÓN Z NO ES EL PROBLEMA"
GEN_Z_CAPTION = (
    "¿La generación Z está dañando el mercado laboral o simplemente está "
    "cuestionando cosas que antes aprendimos a aceptar?\n\n"
    "La respuesta no está en más autoridad. Está en mejores líderes, más claridad "
    "y una razón para hacer el trabajo.\n\n"
    "La generación Z no es rebelde. Es exigente.\n\n"
    "¿Tú qué opinas?\n\n"
    "#liderazgo"
)
GEN_Z_BODY = f"Título:\n\n{GEN_Z_TITLE}\n\n{GEN_Z_CAPTION}"
SCRIPT_THEN_COPY = (
    "Lo que la generación Z está haciendo en el mercado laboral es aterrador.\n\n"
    "El 70% de los líderes ya consideró dejar su cargo.\n\n"
    f"{GEN_Z_BODY}"
)
OWN_CAPTION = (
    "Caption ya escrito en la propiedad, con texto suficiente para publicarlo tal cual."
)


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


def _row(**overrides):
    base = dict(
        page_id=PAGE,
        url="https://notion.so/gen-z",
        channel="TikTok, Instagram, Youtube Shorts",
        channels=["TikTok", "Instagram", "Youtube Shorts"],
        title="Convertir: Los directivos perdieron la esperanza en la Generación Z (1)",
        cover_text="Hook ya listo",
        caption=OWN_CAPTION,
        final_file_url="https://drive.google.com/file/d/gen-z/view",
        content_type="Reels - Tik Tok - Shorts",
        publication=datetime(2026, 10, 17, 12, 0, tzinfo=BOG),
        miniatura_url=None,
        status="Aprobado - Edición Final",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(tmp_path, rows, *, posts=(), dry=False, now=NOW, body=""):
    settings = _settings(tmp_path)
    notion, metricool = MagicMock(), MagicMock()
    notion.fetch_approved_current_week.return_value = rows
    notion.fetch_linked_in_window.return_value = []
    notion.page_body_plain_text.return_value = body
    metricool.get_scheduled_posts.return_value = list(posts)
    metricool.create_scheduled_post.return_value = {"data": {"id": 99, "uuid": "u-99"}}
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
    return stats, notion, metricool, slack


def _reasons(slack):
    return [call.kwargs["reason"] for call in slack.call_args_list]


def _guard(tmp_path) -> ScheduleGuard:
    return ScheduleGuard(tmp_path / "schedule-guard.sqlite3")


# Parser -------------------------------------------------------------------


def test_gen_z_body_is_a_confident_title_and_caption():
    parsed = parse_body_copy(GEN_Z_BODY)
    assert parsed is not None and parsed.confident
    assert parsed.titulo == GEN_Z_TITLE
    assert parsed.caption == GEN_Z_CAPTION
    assert "aterror" not in (parsed.caption or "")


def test_markdown_titulo_and_heading_match_the_gen_z_piece():
    body = (
        "**Título:**\n"
        f"# {GEN_Z_TITLE}\n"
        "¿La generación Z está dañando el mercado laboral o simplemente está "
        "cuestionando cosas que antes aprendimos a aceptar?\n"
        "La respuesta no está en más autoridad."
    )
    parsed = parse_body_copy(body)
    assert parsed is not None and parsed.confident
    assert parsed.titulo == GEN_Z_TITLE
    assert parsed.caption.startswith("¿La generación Z está dañando")
    assert "La respuesta no está" in (parsed.caption or "")


def test_same_line_title_and_caption_below_are_confident():
    body = (
        f"Titulo: {GEN_Z_TITLE}\n\n"
        "¿La generación Z está dañando el mercado laboral o simplemente está "
        "cuestionando cosas que antes aprendimos a aceptar?"
    )
    parsed = parse_body_copy(body)
    assert parsed is not None and parsed.confident
    assert parsed.titulo == GEN_Z_TITLE


def test_script_above_the_label_is_not_the_caption():
    parsed = parse_body_copy(SCRIPT_THEN_COPY)
    assert parsed is not None and parsed.confident
    assert parsed.titulo == GEN_Z_TITLE
    assert "aterror" not in (parsed.caption or "")
    assert "70%" not in (parsed.caption or "")


def test_explicit_caption_label_is_stripped():
    body = (
        f"Título: {GEN_Z_TITLE}\n\n"
        "Caption:\n"
        "¿La generación Z está dañando el mercado laboral o simplemente está "
        "cuestionando cosas que antes aprendimos a aceptar?"
    )
    parsed = parse_body_copy(body)
    assert parsed is not None and parsed.confident
    assert not (parsed.caption or "").startswith("Caption")


def test_no_titulo_line_is_not_a_match():
    assert parse_body_copy("") is None
    assert parse_body_copy("   ") is None
    assert parse_body_copy("Solo el caption, sin etiqueta de título, listo para publicar.") is None
    assert parse_body_copy("Título de la publicación: esto es otra propiedad") is None


def test_bare_titulo_line_is_not_confident():
    body = "Lo que la generación Z está haciendo es aterrador.\n\nTítulo:\n"
    parsed = parse_body_copy(body)
    assert parsed is not None and not parsed.confident
    assert parsed.titulo is None
    assert parsed.caption is None


def test_two_titulo_blocks_propose_the_last_one_and_are_not_confident():
    body = (
        f"{GEN_Z_BODY}\n\n"
        "Título:\n\n"
        "OTRO TITULO CORTO\n\n"
        "Este segundo caption también es largo y claro para que una persona lo confirme."
    )
    parsed = parse_body_copy(body)
    assert parsed is not None and not parsed.confident
    assert parsed.titulo == "OTRO TITULO CORTO"
    assert "segundo caption" in (parsed.caption or "")


def test_a_long_paragraph_is_not_a_confident_title():
    sentence = (
        "Esta es una oración larga que claramente es el caption del video y no "
        "un título corto de portada para YouTube o para la miniatura."
    )
    parsed = parse_body_copy(f"Título:\n\n{sentence}\n")
    assert parsed is not None and not parsed.confident
    assert parsed.titulo == sentence


def test_page_body_plain_text_skips_the_script_table(tmp_path):
    repo = NotionRepository(_settings(tmp_path))
    repo._client = MagicMock()
    repo._client.blocks.children.list.return_value = {
        "has_more": False,
        "results": [
            {"type": "table", "table": {"table_width": 2}},
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Título:"}]}},
            {
                "type": "heading_1",
                "heading_1": {"rich_text": [{"plain_text": GEN_Z_TITLE}]},
            },
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "plain_text": (
                                "¿La generación Z está dañando el mercado laboral o "
                                "simplemente está cuestionando cosas que antes aprendimos "
                                "a aceptar?"
                            )
                        }
                    ]
                },
            },
        ],
    }
    parsed = parse_body_copy(repo.page_body_plain_text("page"))
    assert parsed is not None and parsed.confident
    assert parsed.titulo == GEN_Z_TITLE
    assert "Hook" not in (parsed.caption or "")


# Schedule -----------------------------------------------------------------


def test_confident_body_is_written_and_scheduled_in_the_same_run(tmp_path):
    row = _row(caption="", cover_text="")
    stats, notion, metricool, slack = _run(tmp_path, [row], body=GEN_Z_BODY)
    assert stats["scheduled"] == 1
    assert stats["skipped"] == 0
    updates = notion.set_rich_text_properties.call_args.args[1]
    assert updates["Caption"] == GEN_Z_CAPTION
    assert updates["Titulo"] == GEN_Z_TITLE
    assert notion.set_rich_text_properties.call_args.kwargs["dry_run"] is False
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["text"] == GEN_Z_CAPTION
    assert body["youtubeData"]["title"] == GEN_Z_TITLE
    assert "copy_needs_confirm" not in _reasons(slack)
    assert "missing_hook" not in _reasons(slack)
    assert "missing_caption" not in _reasons(slack)


def test_non_empty_properties_are_not_overwritten(tmp_path):
    row = _row()
    stats, notion, metricool, _slack = _run(tmp_path, [row], body=GEN_Z_BODY)
    assert stats["scheduled"] == 1
    notion.page_body_plain_text.assert_not_called()
    notion.set_rich_text_properties.assert_not_called()
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["text"] == OWN_CAPTION
    assert body["youtubeData"]["title"] == "Hook ya listo"


def test_only_the_empty_titulo_is_written(tmp_path):
    row = _row(cover_text="")
    stats, notion, metricool, _slack = _run(tmp_path, [row], body=GEN_Z_BODY)
    assert stats["scheduled"] == 1
    updates = notion.set_rich_text_properties.call_args.args[1]
    assert list(updates) == ["Titulo"]
    assert updates["Titulo"] == GEN_Z_TITLE
    body = metricool.create_scheduled_post.call_args.args[0]
    assert body["text"] == OWN_CAPTION
    assert body["youtubeData"]["title"] == GEN_Z_TITLE


def test_uncertain_body_skips_and_shows_the_proposal(tmp_path):
    body = (
        f"{GEN_Z_BODY}\n\nTítulo:\n\nOTRO TITULO CORTO\n\n"
        "Este segundo caption también es largo y claro para que una persona lo confirme."
    )
    row = _row(caption="", cover_text="")
    stats, notion, metricool, slack = _run(tmp_path, [row], body=body)
    assert stats["scheduled"] == 0
    assert stats["skipped"] == 1
    notion.set_rich_text_properties.assert_not_called()
    metricool.create_scheduled_post.assert_not_called()
    call = [c for c in slack.call_args_list if c.kwargs["reason"] == "copy_needs_confirm"][0]
    message = call.kwargs["message"]
    assert "OTRO TITULO CORTO" in message
    assert "segundo caption" in message
    assert "no estoy seguro" in message
    assert row.caption == ""
    assert row.cover_text == ""


def test_story_without_caption_or_titulo_still_schedules(tmp_path):
    row = _row(
        channel="Instagram",
        channels=["Instagram"],
        caption="",
        cover_text="",
        title="",
        content_type="Historias",
        final_file_url="https://cdn.example/story.jpg",
    )
    stats, notion, metricool, slack = _run(tmp_path, [row], body="")
    assert stats["scheduled"] == 1
    assert metricool.create_scheduled_post.call_args.args[0]["text"] == ""
    notion.set_rich_text_properties.assert_not_called()
    assert "missing_caption" not in _reasons(slack)
    assert "missing_hook" not in _reasons(slack)


def test_uncertain_body_does_not_block_an_instagram_story(tmp_path):
    body = (
        f"{GEN_Z_BODY}\n\nTítulo:\n\nOTRO TITULO CORTO\n\n"
        "Este segundo caption también es largo y claro para que una persona lo confirme."
    )
    row = _row(
        channel="Instagram",
        channels=["Instagram"],
        caption="",
        cover_text="",
        title="",
        content_type="Historias",
    )
    stats, notion, metricool, slack = _run(tmp_path, [row], body=body)
    assert stats["scheduled"] == 1
    assert metricool.create_scheduled_post.call_args.args[0]["text"] == ""
    notion.set_rich_text_properties.assert_not_called()
    assert "copy_needs_confirm" not in _reasons(slack)


def test_body_without_a_titulo_line_stays_a_caption_fallback(tmp_path):
    body = (
        "Este texto del cuerpo no trae una etiqueta de título pero sí alcanza "
        "para el caption del video."
    )
    row = _row(caption="", cover_text="Hook ya listo", channel="TikTok", channels=["TikTok"])
    stats, notion, metricool, _slack = _run(tmp_path, [row], body=body)
    assert stats["scheduled"] == 1
    notion.set_rich_text_properties.assert_not_called()
    assert metricool.create_scheduled_post.call_args.args[0]["text"] == body


def test_dry_run_adopts_the_copy_without_writing_notion(tmp_path):
    row = _row(caption="", cover_text="")
    stats, notion, metricool, _slack = _run(tmp_path, [row], body=GEN_Z_BODY, dry=True)
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_not_called()
    assert notion.set_rich_text_properties.call_args.kwargs["dry_run"] is True
    assert row.caption == GEN_Z_CAPTION
    assert row.cover_text == GEN_Z_TITLE


def test_expired_uncertain_body_keeps_the_expiry_alert(tmp_path):
    row = _row(
        caption="",
        cover_text="",
        publication=datetime(2026, 10, 15, 12, 0, tzinfo=BOG),
    )
    body = "Título:\n\n"
    stats, notion, metricool, slack = _run(tmp_path, [row], body=body)
    assert stats["skipped"] == 1
    assert "past_publication_date" in _reasons(slack)
    assert "copy_needs_confirm" not in _reasons(slack)
    message = slack.call_args.kwargs["message"]
    assert "ya pasó" in message
    assert "hora futura" in message
    metricool.create_scheduled_post.assert_not_called()
    notion.set_rich_text_properties.assert_not_called()


def test_expired_confident_body_is_saved_and_not_scheduled(tmp_path):
    row = _row(
        caption="",
        cover_text="",
        publication=datetime(2026, 10, 15, 12, 0, tzinfo=BOG),
    )
    _guard(tmp_path).reserve(BLOG, row.page_id)
    stats, notion, metricool, slack = _run(tmp_path, [row], body=GEN_Z_BODY)
    assert stats["skipped"] == 1
    assert stats["scheduled"] == 0
    updates = notion.set_rich_text_properties.call_args.args[1]
    assert updates["Caption"] == GEN_Z_CAPTION
    assert updates["Titulo"] == GEN_Z_TITLE
    metricool.create_scheduled_post.assert_not_called()
    message = [c for c in slack.call_args_list if c.kwargs["reason"] == "past_publication_date"][0]
    assert "ya pasó" in message.kwargs["message"]
    assert not _guard(tmp_path).contains(BLOG, row.page_id)


def test_expired_piece_then_a_new_future_time_is_scheduled(tmp_path):
    past = _row(publication=datetime(2026, 10, 15, 12, 0, tzinfo=BOG))
    _guard(tmp_path).reserve(BLOG, past.page_id)
    stats, _notion, metricool, slack = _run(tmp_path, [past])
    assert stats["skipped"] == 1
    assert "past_publication_date" in _reasons(slack)
    metricool.create_scheduled_post.assert_not_called()
    assert not _guard(tmp_path).contains(BLOG, past.page_id)

    future = _row(publication=NOW + timedelta(hours=5))
    stats, _notion, metricool, slack = _run(tmp_path, [future])
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_called_once()
    assert "prior_attempt" not in _reasons(slack)


def test_a_different_reserved_time_does_not_block_the_new_one(tmp_path):
    future = _row(publication=NOW + timedelta(hours=5))
    old = datetime(2026, 10, 15, 12, 0, tzinfo=BOG)
    assert _guard(tmp_path).reserve(BLOG, future.page_id, old)
    stats, _notion, metricool, slack = _run(tmp_path, [future])
    assert stats["scheduled"] == 1
    metricool.create_scheduled_post.assert_called_once()
    assert "prior_attempt" not in _reasons(slack)


def test_the_same_reserved_time_still_blocks(tmp_path):
    pub = NOW + timedelta(hours=5)
    row = _row(publication=pub)
    assert _guard(tmp_path).reserve(BLOG, row.page_id, pub)
    stats, _notion, metricool, slack = _run(tmp_path, [row])
    assert stats["scheduled"] == 0
    metricool.create_scheduled_post.assert_not_called()
    assert "prior_attempt" in _reasons(slack)


def test_legacy_lock_without_a_time_still_blocks_until_expiry(tmp_path):
    future = _row(publication=NOW + timedelta(hours=5))
    assert _guard(tmp_path).reserve(BLOG, future.page_id)
    stats, _notion, metricool, slack = _run(tmp_path, [future])
    assert stats["scheduled"] == 0
    assert "prior_attempt" in _reasons(slack)
    metricool.create_scheduled_post.assert_not_called()
    assert _guard(tmp_path).contains(BLOG, future.page_id)


# Digest and Notion write --------------------------------------------------


def test_digest_adds_one_como_arreglar_line_for_copy_and_expiry():
    digest = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    digest.add(notion_page_id="a", reason="missing_hook", message="falta Titulo")
    digest.add(notion_page_id="b", reason="missing_caption", message="falta Caption")
    digest.add(notion_page_id="c", reason="past_publication_date", message="ya pasó")
    digest.add(notion_page_id="d", reason="scheduled", message="programada")
    text = digest.render()
    assert text.count("Cómo arreglar:") == 1
    assert 'llena "Titulo" y "Caption"' in text
    assert 'fecha y hora futura en "Publicación"' in text
    assert "programada" in text


def test_digest_omits_como_arreglar_for_other_reasons():
    digest = SlackDigest(webhook_url="https://hooks.slack.test/x", channel="#c", header="H")
    digest.add(notion_page_id="a", reason="missing_time", message="sin hora")
    digest.add(notion_page_id="b", reason="slot_conflict", message="choque")
    assert "Cómo arreglar" not in digest.render()


def test_set_rich_text_chunks_and_skips_blank_fields(tmp_path):
    repo = NotionRepository(_settings(tmp_path))
    repo._client = MagicMock()
    long = "a" * 2500
    assert repo.set_rich_text_properties(
        "p", {"Caption": long, "Titulo": "  "}, dry_run=False
    )
    props = repo._client.pages.update.call_args.kwargs["properties"]
    assert "Titulo" not in props
    chunks = props["Caption"]["rich_text"]
    assert [len(part["text"]["content"]) for part in chunks] == [2000, 500]
    repo._client.pages.update.reset_mock()
    assert repo.set_rich_text_properties("p", {"Caption": "hola"}, dry_run=True) is False
    repo._client.pages.update.assert_not_called()


def test_set_rich_text_tolerates_a_missing_property(tmp_path):
    repo = NotionRepository(_settings(tmp_path))
    repo._client = MagicMock()
    repo._client.pages.update.side_effect = APIResponseError(
        code="validation_error",
        status=400,
        message="Caption is not a property that exists.",
        headers=httpx.Headers(),
        raw_body_text="{}",
    )
    assert repo.set_rich_text_properties("p", {"Caption": "hola"}, dry_run=False) is False
