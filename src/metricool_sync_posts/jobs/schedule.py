"""Job 1: Aprobado - Edición Final → Metricool → Notion Programado."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from metricool_sync_posts.build_info import build_label
from metricool_sync_posts.config import Settings
from metricool_sync_posts.cover.reel import resolve_instagram_reel_cover
from metricool_sync_posts.jobs.common import (
    caption_for_row,
    caption_has_placeholder,
    publication_dt,
)
from metricool_sync_posts.jobs.content_types import (
    build_schedule_body,
    is_miniatura_type,
    is_story_type,
    partition_image_networks,
    publication_format_groups,
    schedule_bodies,
    video_network_skip_reason,
)
from metricool_sync_posts.jobs.schedule_guard import ScheduleGuard
from metricool_sync_posts.media.errors import MediaHostError
from metricool_sync_posts.media.final_file import (
    FinalFileKind,
    classify_final_file,
    invalid_archivo_final_message,
)
from metricool_sync_posts.media.resolve import prepare_media_urls_for_metricool
from metricool_sync_posts.metricool.channels import canal_labels, normalize_channel, plan_canals
from metricool_sync_posts.metricool.client import MetricoolClient
from metricool_sync_posts.metricool.matching import (
    SLOT_MATCH_MINUTES,
    claim_keys_for_post,
    combined_post_state,
    covers_youtube_split,
    describe_format_groups,
    find_duplicate_candidates,
    find_slot_occupants,
    format_match_evidence,
    missing_youtube_split_networks,
    nearest_network_post,
    post_copy_is_thin,
    post_format_groups,
    post_stable_uuid,
    slot_occupant_blocks,
    split_stored_refs,
)
from metricool_sync_posts.metricool.profiles import load_connected_networks
from metricool_sync_posts.notion.client import NotionRepository
from metricool_sync_posts.slack.dedupe import DedupeStore
from metricool_sync_posts.slack.digest import SlackDigest
from metricool_sync_posts.slack.notify import notify_slack
from metricool_sync_posts.timeutil import (
    dates_equal_within_minutes,
    now_in,
    publication_sort_key,
    rolling_window_bounds,
)

logger = logging.getLogger(__name__)

# Business rules, not just defaults: Newsletter stays manual, IG Nico is another brand.
_ALWAYS_EXCLUDED = frozenset({"Newsletter", "IG Nico"})

# Spanish explanation of each skip reason, used by the expiry alert to name
# the blocker that kept the piece from being scheduled in time.
REASON_LABELS_ES: dict[str, str] = {
    "no_connected_network": 'falta "Canal" (o la red no está conectada)',
    "missing_publication_date": 'falta "Publicación"',
    "missing_time": '"Publicación" no tiene hora',
    "placeholder_caption": "el Caption tiene un texto de relleno",
    "missing_caption": "falta el Caption",
    "slot_conflict": "choque de horario con otra pieza",
    "ambiguous_duplicate": "varios posts de Metricool parecen ser esta pieza",
    "invalid_archivo_final": '"Archivo Final" no tiene un link válido',
    "missing_hook": 'falta "Titulo" (YouTube)',
    "prior_attempt": "un intento anterior de programarla quedó bloqueado",
    "media_host_failed": "no se pudo descargar o subir el archivo",
    "missing_media": "no se encontró el archivo",
    "miniatura_not_a_post": "es una miniatura, no un post",
    "schedule_error": "error de Metricool al programar",
}


def _piece_label(row) -> str:
    """Slack mrkdwn link to the Notion page, named by its title."""
    url = getattr(row, "url", None) or ""
    title = (getattr(row, "title", None) or "").strip() or "Pieza sin nombre"
    title = title.replace("|", "/").replace(">", "›").replace("<", "‹")
    if url:
        return f"<{url}|{title}>"
    return f"{title} ({row.page_id})"


def _alert(ctx: dict, row, reason: str, text: str) -> None:
    """One Spanish alert per piece+reason, queued into the run digest."""
    notify_slack(
        webhook_url=ctx["settings"].slack_webhook_url,
        channel=ctx["settings"].slack_channel,
        dedupe=ctx["dedupe"],
        notion_page_id=row.page_id,
        reason=reason,
        message=f"{_piece_label(row)}: {text}",
        dry_run=ctx["dry"],
        digest=ctx["digest"],
        title=getattr(row, "title", None),
        url=getattr(row, "url", None),
    )


def _fmt_local(dt: datetime) -> str:
    return dt.strftime("%d/%m %I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m.")


def _expired_text(ctx: dict, row, pub: datetime) -> str:
    previous = [
        REASON_LABELS_ES[r]
        for r in ctx["dedupe"].reasons_for(row.page_id)
        if r in REASON_LABELS_ES
    ]
    why = ""
    if previous:
        unique = list(dict.fromkeys(previous))
        why = " Antes no se programó porque " + "; ".join(unique) + "."
    return (
        f'la hora de "Publicación" ({_fmt_local(pub)}) ya pasó y la pieza sigue en '
        f"Aprobado sin programar.{why} Para publicarla, corrige lo que falte y pon una "
        'fecha y hora futura en "Publicación"; la próxima corrida (cada 2 h) la programa.'
    )


def _created_refs(responses: list) -> tuple[list[str], list[str]]:
    """Metricool create responses → (ids, uuids). Tolerates {"data": {...}}."""
    ids: list[str] = []
    uuids: list[str] = []
    for resp in responses:
        item = resp
        if isinstance(item, dict) and isinstance(item.get("data"), dict):
            item = item["data"]
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or item.get("postId") or "").strip()
        uid = str(item.get("uuid") or "").strip()
        if pid:
            ids.append(pid)
        if uid:
            uuids.append(uid)
    return ids, uuids


def _store_refs(notion, row, ids: list[str], uuids: list[str], dry: bool) -> None:
    setter = getattr(notion, "set_metricool_refs", None)
    if not callable(setter) or not (ids or uuids):
        return
    try:
        setter(row.page_id, ids=ids, uuids=uuids, dry_run=dry)
    except Exception:
        logger.exception("Could not store Metricool ids on %s", row.page_id)


def _publication_on_date(row, tz_name: str, target: date) -> bool:
    pub = publication_dt(row, tz_name)
    if pub is None:
        return False
    return pub.astimezone(ZoneInfo(tz_name)).date() == target


def _norm_channel(name: str | None) -> str:
    """Normalize Canal labels for exclude matching (case/space insensitive)."""
    if not name:
        return ""
    collapsed = re.sub(r"[\s\u00a0]+", " ", name).strip()
    return collapsed.casefold()


def _channel_is_excluded(channel: str | None, exclude: frozenset[str]) -> bool:
    if not exclude:
        return False
    norms = {_norm_channel(x) for x in exclude if _norm_channel(x)}
    return _norm_channel(channel) in norms


def _is_linkedin_only_canal(channel: str | None) -> bool:
    """True for Canal values that are LinkedIn and nothing else."""
    if not channel or "linkedin" not in _norm_channel(channel):
        return False
    return normalize_channel(channel) == "linkedin"


def _row_excluded(channel: str | None, exclude: frozenset[str]) -> bool:
    return _channel_is_excluded(channel, exclude) or _is_linkedin_only_canal(channel)


def _row_should_drop(row, exclude: frozenset[str]) -> bool:
    """IG Nico skips the whole row before media. Other Canal values stay for the loop."""
    labels = canal_labels(row)
    if not labels:
        return False
    return plan_canals(labels, exclude=exclude).skip_nico


def _as_post_list(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _post_key(post: dict) -> str:
    return str(post.get("id") or post.get("postId") or "")


def _remember_claim(claimed: dict[str, str], post: dict, page_id: str) -> None:
    """Claim by uuid and by the current id.

    update_scheduled_post keeps the uuid and returns a new id, so a claim
    stored only under the old id would miss the same post on the next read.
    """
    for key in claim_keys_for_post(post):
        claimed.setdefault(key, page_id)


def _claimed_by(claimed: dict[str, str], post: dict) -> bool:
    return any(key in claimed for key in claim_keys_for_post(post))


def _gap_minutes(delta: float | None) -> str:
    if delta is None:
        return "-"
    return str(int(round(delta)))


def _peer_slot_conflicts(
    taken: list[dict],
    *,
    publication: datetime,
    groups: frozenset[str],
) -> list[dict]:
    """Rows already scheduled this run that share a format group inside ±15 min."""
    if not groups:
        return []
    hits: list[dict] = []
    for slot in taken:
        if not (groups & slot["groups"]):
            continue
        if dates_equal_within_minutes(publication, slot["publication"], SLOT_MATCH_MINUTES):
            hits.append(slot)
    return hits


def _conflict_parties(
    *,
    groups: frozenset[str],
    blocking: list[dict],
    peers: list[dict],
) -> str:
    """Name each real conflict with the format group it shares."""
    parts: list[str] = []
    for post in blocking:
        shared = groups & post_format_groups(post)
        parts.append(f"Metricool {_post_key(post)} ({describe_format_groups(shared)})")
    for slot in peers:
        shared = groups & slot["groups"]
        label = slot.get("url") or slot["page_id"]
        parts.append(f"{label} ({describe_format_groups(shared)})")
    if not parts:
        return "another piece"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _log_slot_guard(
    posts: list[dict],
    *,
    row,
    networks: list[str],
    tz_name: str,
    notion_publication,
    occupants: list[dict],
    blocking: list[dict],
    groups: frozenset[str] | set[str] | None = None,
    peer_conflicts: int = 0,
) -> None:
    """One line per candidate, including rows with nothing in the ±15 min slot."""
    nearest, nearest_delta = nearest_network_post(
        posts,
        network=None,
        networks=networks,
        tz_name=tz_name,
        notion_publication=notion_publication,
    )
    thin, thin_delta = nearest_network_post(
        posts,
        network=None,
        networks=networks,
        tz_name=tz_name,
        notion_publication=notion_publication,
        thin_only=True,
    )
    if not occupants and not peer_conflicts:
        reason = "none"
    elif blocking or peer_conflicts:
        reason = "slot_conflict"
    else:
        reason = "cleared"
    logger.info(
        "Slot Notion %s reason=%s occupants=%s peers=%s groups=%s notion=%s "
        "nearest=%s delta_min=%s nearest_thin=%s thin_delta_min=%s",
        row.page_id,
        reason,
        len(occupants),
        peer_conflicts,
        ",".join(sorted(groups or ())) or "-",
        notion_publication.isoformat(timespec="seconds"),
        _post_key(nearest) if nearest else "-",
        _gap_minutes(nearest_delta),
        _post_key(thin) if thin else "-",
        _gap_minutes(thin_delta),
    )


def _linked_rows(notion, window_start, window_end) -> list:
    """Programado/Publicado rows. A missing or unexpected client result means none."""
    fetcher = getattr(notion, "fetch_linked_in_window", None)
    if not callable(fetcher):
        return []
    try:
        rows = fetcher(window_start, window_end)
    except Exception:
        logger.exception("Could not load Programado/Publicado rows for duplicate claims")
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if getattr(row, "page_id", None)]


def _claim_linked_posts(
    *,
    notion,
    linked_rows: list,
    approved_ids: set[str],
    existing_posts: list[dict],
    exclude: frozenset[str],
    tz_name: str,
    connected: frozenset[str],
) -> dict[str, str]:
    """Metricool uuid/id → Notion page that already owns that post."""
    claimed: dict[str, str] = {}
    for linked in linked_rows:
        if linked.page_id in approved_ids:
            continue
        try:
            caption = caption_for_row(notion, linked)
        except Exception:
            logger.exception("Caption failed for linked row %s", linked.page_id)
            continue
        if not caption:
            continue
        plan = plan_canals(canal_labels(linked), exclude=exclude, connected=connected)
        if plan.skip_nico or not plan.networks:
            continue
        hits = find_duplicate_candidates(
            existing_posts,
            caption=caption,
            title=getattr(linked, "title", None),
            network=None,
            networks=list(plan.networks),
            tz_name=tz_name,
            notion_publication=publication_dt(linked, tz_name),
            media_urls=[linked.final_file_url] if getattr(linked, "final_file_url", None) else None,
        )
        stored_id = str(getattr(linked, "metricool_id", None) or "").strip()
        stored_uuid = str(getattr(linked, "metricool_uuid", None) or "").strip()
        if stored_id or stored_uuid:
            for post in existing_posts:
                post_uuid = post_stable_uuid(post)
                # Uuid wins. A stored numeric id may be the pre-update id.
                if stored_uuid and post_uuid and post_uuid in split_stored_refs(stored_uuid):
                    hits.append(post)
                elif (
                    stored_id
                    and not stored_uuid
                    and _post_key(post) in split_stored_refs(stored_id)
                ):
                    hits.append(post)
        for hit in hits:
            if not claim_keys_for_post(hit) or _claimed_by(claimed, hit):
                continue
            _remember_claim(claimed, hit, linked.page_id)
            logger.info(
                "Match Notion %s -> Metricool %s %s",
                linked.page_id,
                post_stable_uuid(hit) or _post_key(hit),
                format_match_evidence(
                    hit,
                    caption=caption,
                    title=getattr(linked, "title", None),
                    tz_name=tz_name,
                    notion_publication=publication_dt(linked, tz_name),
                    networks=list(plan.networks),
                    media_urls=(
                        [linked.final_file_url] if getattr(linked, "final_file_url", None) else None
                    ),
                    stored_id=stored_id or None,
                    stored_uuid=stored_uuid or None,
                ),
            )
    return claimed


def _reconcile_existing(
    *,
    settings: Settings,
    notion: NotionRepository,
    dedupe: DedupeStore,
    row,
    post: dict,
    dry: bool,
    posts: list[dict] | None = None,
    digest: SlackDigest | None = None,
) -> None:
    """Point Notion at the post that already exists. Never creates another one.

    Callers only get here with a confident match (stored id, or caption/title
    similarity). A post that merely shares the slot is never reconciled.
    """
    group = posts or [post]
    state = combined_post_state(group)
    post_id = ", ".join(
        str(item.get("id") or item.get("postId") or "")
        for item in group
        if item.get("id") or item.get("postId")
    )
    if state == "ERROR":
        logger.error(
            "BLOCKER: Metricool post %s for %s has a provider ERROR; left as %s",
            post_id,
            row.page_id,
            row.status,
        )
        notify_slack(
            webhook_url=settings.slack_webhook_url,
            channel=settings.slack_channel,
            dedupe=dedupe,
            notion_page_id=row.page_id,
            reason="metricool_duplicate_error",
            message=(
                f"{_piece_label(row)}: ya existe en Metricool (post {post_id}) pero salió "
                "con ERROR. No se creó otra vez y Notion no se cambió. Revisa el post en "
                "Metricool y vuelve a publicarlo desde allí."
            ),
            dry_run=dry,
            digest=digest,
        )
        return
    if state == "PUBLISHED":
        target = settings.notion_status_published
    else:
        # PENDING / SCHEDULED / UNKNOWN still sitting in the calendar.
        target = settings.notion_status_scheduled
    if dry:
        logger.info(
            "[dry-run] Would set Notion %s to %s (existing Metricool %s %s)",
            row.page_id,
            target,
            post_id,
            state,
        )
        return
    notion.set_status(row.page_id, target, dry_run=False)
    logger.info(
        "Existing Metricool post %s (%s) for %s; Notion set to %s",
        post_id,
        state,
        row.page_id,
        target,
    )
    _store_refs(
        notion,
        row,
        [str(i.get("id") or i.get("postId")) for i in group if i.get("id") or i.get("postId")],
        [post_stable_uuid(i) for i in group if post_stable_uuid(i)],
        dry,
    )


def run_schedule(
    *,
    settings: Settings,
    dry_run: bool | None = None,
    only_publication_date: date | None = None,
    only_page_id: str | None = None,
    exclude_channels: frozenset[str] | None = frozenset(),
) -> dict[str, int]:
    # Validate before opening clients or running any pipeline step.
    target_page = str(UUID(only_page_id)) if only_page_id is not None else None
    dry = settings.dry_run if dry_run is None else dry_run
    stats = {"queried": 0, "scheduled": 0, "skipped": 0, "errors": 0, "reconciled": 0}

    if not settings.enable_schedule:
        logger.warning("Schedule job disabled (ENABLE_SCHEDULE=false). Exiting.")
        return stats

    logger.info("metricool_sync_posts build: %s", build_label())

    notion = NotionRepository(settings)
    metricool = MetricoolClient(settings)
    dedupe = DedupeStore(
        path=settings.slack_dedupe_file,
        ttl_seconds=settings.slack_dedupe_hours * 3600,
    )
    settings.ensure_data_dirs()
    connected = load_connected_networks(metricool, settings.metricool_blog_id)

    digest = SlackDigest(
        webhook_url=settings.slack_webhook_url,
        channel=settings.slack_channel,
        header="",
    )
    ctx = {"settings": settings, "dedupe": dedupe, "dry": dry, "digest": digest}

    now = now_in(settings.timezone)
    # Rolling window: recent past (expiry alerts) through the next N days, so
    # next week's pieces are seen before Monday. Successful creates are capped.
    week_start, week_end = rolling_window_bounds(
        now,
        settings.timezone,
        back_days=settings.schedule_lookback_days,
        ahead_days=settings.schedule_lookahead_days,
    )
    digest.header = (
        f":calendar: *Programación CTW — corrida {now.strftime('%d/%m %H:%M')}* "
        "(solo avisos nuevos o que cambiaron)"
    )
    rows = notion.fetch_approved_current_week(week_start, week_end, limit=None)
    if target_page:
        rows = [r for r in rows if str(UUID(r.page_id)) == target_page]
        if len(rows) != 1:
            metricool.close()
            raise ValueError("--only-page-id must match exactly one approved row in the window")
    if only_publication_date is not None:
        tz = settings.timezone
        rows = [r for r in rows if _publication_on_date(r, tz, only_publication_date)]
    exclude = (
        settings.schedule_exclude_channels_set()
        | (exclude_channels or frozenset())
        | _ALWAYS_EXCLUDED
    )
    if exclude:
        before = len(rows)
        kept = []
        dropped: list[str] = []
        for r in rows:
            if _row_should_drop(r, exclude):
                dropped.append(f"{(r.channel or '').strip() or '?'}:{r.page_id[:8]}")
                continue
            kept.append(r)
        rows = kept
        logger.info(
            "Excluded channels filter: %s — dropped %s/%s before media (%s); remaining %s",
            sorted(exclude),
            len(dropped),
            before,
            dropped,
            len(rows),
        )
    if target_page and len(rows) != 1:
        metricool.close()
        raise ValueError("--only-page-id row does not meet date/channel filters")
    rows.sort(
        key=lambda r: (
            publication_sort_key(r.publication, week_start),
            r.page_id,
        )
    )
    stats["queried"] = len(rows)
    logger.info(
        "Found %s approved rows for window %s — %s (channels: %s)",
        len(rows),
        week_start.date(),
        week_end.date(),
        [(r.channel, r.page_id[:8]) for r in rows],
    )

    guard = ScheduleGuard(settings.media_work_dir.parent / "schedule-guard.sqlite3")
    # --only-date / --only-page-id still schedule the filtered set; the default
    # run stops after SCHEDULE_MAX_PER_RUN successful creates.
    cap: int | None
    if only_publication_date is not None or target_page is not None:
        cap = None
    else:
        cap = settings.schedule_max_per_run

    existing_posts: list[dict] = []
    if rows:
        try:
            raw_posts = metricool.get_scheduled_posts(
                week_start - timedelta(days=7),
                week_end + timedelta(days=7),
                extended_range=True,
            )
        except Exception:
            logger.exception("Metricool duplicate lookup failed; not creating posts this run")
            stats["errors"] += 1
            metricool.close()
            return stats
        existing_posts = _as_post_list(raw_posts)
        logger.info("Loaded %s Metricool posts for duplicate check", len(existing_posts))

    claimed = _claim_linked_posts(
        notion=notion,
        linked_rows=_linked_rows(
            notion, week_start - timedelta(days=7), week_end + timedelta(days=7)
        ),
        approved_ids={row.page_id for row in rows},
        existing_posts=existing_posts,
        exclude=exclude,
        tz_name=settings.timezone,
        connected=connected,
    )
    if claimed:
        logger.info("Metricool posts already claimed by other Notion rows: %s", sorted(claimed))

    # Pieces this run has already accepted. A later row conflicts only when it
    # shares one of these format groups inside the 15-minute window.
    taken_slots: list[dict] = []

    for row in rows:
        try:
            labels = canal_labels(row)
            plan = plan_canals(labels, exclude=exclude, connected=connected)
            if plan.skip_nico:
                stats["skipped"] += 1
                logger.warning("Skip %s: IG Nico is not scheduled", row.page_id)
                continue
            if plan.dropped:
                logger.info(
                    "Dropped Canal values for %s (not connected on this brand): %s",
                    row.page_id,
                    list(plan.dropped),
                )
            if not plan.networks:
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: no connected network remains after dropping %s",
                    row.page_id,
                    list(plan.dropped) or labels or None,
                )
                if labels:
                    detail = (
                        f'"Canal" dice {", ".join(labels)}, pero ninguna de esas redes se '
                        "programa desde aquí (solo IG colombiatechoficial, TikTok y YouTube)."
                    )
                else:
                    detail = '"Canal" está vacío, así que no sé a qué red mandarla.'
                _alert(
                    ctx,
                    row,
                    "no_connected_network",
                    f"no se programó: {detail} Llena \"Canal\" en Notion.",
                )
                continue
            networks = list(plan.networks)
            completing_split = False
            pub = publication_dt(row, settings.timezone)
            if pub is None:
                stats["skipped"] += 1
                logger.warning("Skip %s: missing Publicación", row.page_id)
                _alert(
                    ctx,
                    row,
                    "missing_publication_date",
                    'no se programó: falta la fecha y hora en "Publicación".',
                )
                continue
            if not isinstance(row.publication, datetime):
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: Publicación %s has no time",
                    row.page_id,
                    row.publication,
                )
                _alert(
                    ctx,
                    row,
                    "missing_time",
                    f'no se programó: "Publicación" tiene fecha ({row.publication}) pero no '
                    "hora. Ponle la hora en Notion.",
                )
                continue
            caption = caption_for_row(notion, row)
            story = is_story_type(row.content_type)
            if caption and caption_has_placeholder(caption):
                stats["skipped"] += 1
                logger.warning("Skip %s: placeholder in caption", row.page_id)
                _alert(
                    ctx,
                    row,
                    "placeholder_caption",
                    "no se programó: el Caption todavía tiene texto de relleno "
                    "([...], {...}, TODO o lorem ipsum). Corrige el Caption en Notion.",
                )
                continue
            now_local = now_in(settings.timezone)
            expired = pub < now_local
            if caption or story:
                raw_hits = find_duplicate_candidates(
                    existing_posts,
                    caption=caption,
                    title=row.title,
                    network=None,
                    networks=networks,
                    tz_name=settings.timezone,
                    notion_publication=pub,
                    media_urls=[row.final_file_url] if row.final_file_url else None,
                )
                hits = [hit for hit in raw_hits if not _claimed_by(claimed, hit)]
                if hits and covers_youtube_split(hits, set(networks)):
                    for hit in hits:
                        logger.info(
                            "Match Notion %s -> Metricool %s %s",
                            row.page_id,
                            _post_key(hit),
                            format_match_evidence(
                                hit,
                                caption=caption,
                                title=row.title,
                                tz_name=settings.timezone,
                                notion_publication=pub,
                                networks=networks,
                                media_urls=(
                                    [row.final_file_url] if row.final_file_url else None
                                ),
                            ),
                        )
                    _reconcile_existing(
                        settings=settings,
                        notion=notion,
                        dedupe=dedupe,
                        row=row,
                        post=hits[0],
                        posts=hits,
                        dry=dry,
                        digest=digest,
                    )
                    for hit in hits:
                        _remember_claim(claimed, hit, row.page_id)
                    stats["reconciled"] += 1
                    continue
                missing_split = missing_youtube_split_networks(hits, set(networks))
                if missing_split:
                    logger.info(
                        "Schedule %s is missing %s after a YouTube/cover split; "
                        "creating only that half",
                        row.page_id,
                        sorted(missing_split),
                    )
                    networks = [net for net in networks if net in missing_split]
                    completing_split = True
                    for hit in hits:
                        _remember_claim(claimed, hit, row.page_id)
                elif len(hits) > 1:
                    stats["skipped"] += 1
                    for hit in hits:
                        logger.info(
                            "Match Notion %s -> Metricool %s %s",
                            row.page_id,
                            _post_key(hit),
                            format_match_evidence(
                                hit,
                                caption=caption,
                                title=row.title,
                                tz_name=settings.timezone,
                                notion_publication=pub,
                                networks=networks,
                                media_urls=(
                                    [row.final_file_url] if row.final_file_url else None
                                ),
                            ),
                        )
                    ids = [hit.get("id") or hit.get("postId") for hit in hits]
                    logger.warning(
                        "Skip %s: ambiguous duplicate, Metricool posts %s",
                        row.page_id,
                        ids,
                    )
                    _alert(
                        ctx,
                        row,
                        "ambiguous_duplicate",
                        f"no se programó: hay varios posts en Metricool que parecen ser esta "
                        f"pieza ({', '.join(str(i) for i in ids)}). Borra los duplicados en "
                        "Metricool; Notion no se cambió.",
                    )
                    continue
                if len(hits) == 1 and not completing_split:
                    chosen = hits[0]
                    logger.info(
                        "Match Notion %s -> Metricool %s %s",
                        row.page_id,
                        _post_key(chosen),
                        format_match_evidence(
                            chosen,
                            caption=caption,
                            title=row.title,
                            tz_name=settings.timezone,
                            notion_publication=pub,
                            networks=networks,
                            media_urls=[row.final_file_url] if row.final_file_url else None,
                        ),
                    )
                    _reconcile_existing(
                        settings=settings,
                        notion=notion,
                        dedupe=dedupe,
                        row=row,
                        post=chosen,
                        dry=dry,
                        digest=digest,
                    )
                    _remember_claim(claimed, chosen, row.page_id)
                    stats["reconciled"] += 1
                    continue
                if expired:
                    # Past pieces never get a slot check: the real problem is the time.
                    stats["skipped"] += 1
                    logger.warning(
                        "Skip %s: publication %s is in the past; expired, not rescheduled",
                        row.page_id,
                        pub,
                    )
                    _alert(ctx, row, "past_publication_date", _expired_text(ctx, row, pub))
                    continue
                groups = publication_format_groups(
                    networks,
                    title=row.title,
                    content_type=row.content_type,
                )
                occupants = find_slot_occupants(
                    existing_posts,
                    network=None,
                    networks=networks,
                    tz_name=settings.timezone,
                    notion_publication=pub,
                    format_groups=groups,
                )
                notion_media = [row.final_file_url] if row.final_file_url else None
                blocking = [
                    occupant
                    for occupant in occupants
                    if slot_occupant_blocks(
                        occupant, story=story, media_urls=notion_media
                    )
                ]
                peers = _peer_slot_conflicts(
                    taken_slots,
                    publication=pub,
                    groups=groups,
                )
                _log_slot_guard(
                    existing_posts,
                    row=row,
                    networks=networks,
                    tz_name=settings.timezone,
                    notion_publication=pub,
                    occupants=occupants,
                    blocking=blocking,
                    groups=groups,
                    peer_conflicts=len(peers),
                )
                for occupant in occupants:
                    if occupant not in blocking:
                        logger.info(
                            "Slot occupant %s does not block %s: media clearly differs",
                            _post_key(occupant),
                            row.page_id,
                        )
                if blocking or peers:
                    stats["skipped"] += 1
                    for occupant in blocking:
                        if post_copy_is_thin(occupant):
                            logger.info(
                                "Slot occupant %s has empty or very short text; "
                                "treating as slot_conflict",
                                _post_key(occupant),
                            )
                        logger.info(
                            "Not the same piece Notion %s Metricool %s %s",
                            row.page_id,
                            _post_key(occupant),
                            format_match_evidence(
                                occupant,
                                caption=caption,
                                title=row.title,
                                tz_name=settings.timezone,
                                notion_publication=pub,
                                networks=networks,
                                media_urls=notion_media,
                            ),
                        )
                    for peer in peers:
                        logger.info(
                            "Slot peer %s blocks %s on %s",
                            peer["page_id"],
                            row.page_id,
                            describe_format_groups(groups & peer["groups"]),
                        )
                    who = _conflict_parties(groups=groups, blocking=blocking, peers=peers)
                    logger.warning(
                        "Skip %s: slot_conflict with %s",
                        row.page_id,
                        who,
                    )
                    empty_slot = any(post_copy_is_thin(post) for post in blocking)
                    if empty_slot:
                        why = "cuyo texto está vacío o es muy corto para compararlo"
                    elif peers and not blocking:
                        why = "que es otra pieza en la misma red y formato"
                    elif story:
                        why = "y esta pieza es una Historia"
                    else:
                        why = "que es otra pieza (el texto no coincide)"
                    _alert(
                        ctx,
                        row,
                        "slot_conflict",
                        f"no se programó: a menos de 15 minutos ya está {who}, {why}. "
                        'Cambia la hora en "Publicación" de Notion (o confirma en Metricool '
                        "si ese post es esta misma pieza). Notion no se cambió.",
                    )
                    continue
            if expired:
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: publication %s is in the past; expired, not rescheduled",
                    row.page_id,
                    pub,
                )
                _alert(ctx, row, "past_publication_date", _expired_text(ctx, row, pub))
                continue
            if not caption and not story:
                stats["skipped"] += 1
                logger.warning("Skip %s: empty caption", row.page_id)
                _alert(
                    ctx,
                    row,
                    "missing_caption",
                    "no se programó: falta el Caption (ni la propiedad ni el cuerpo de la "
                    "página tienen texto). Llena el Caption en Notion.",
                )
                continue
            if is_miniatura_type(row.content_type):
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: content type %r is a thumbnail, not a post",
                    row.page_id,
                    row.content_type,
                )
                _alert(
                    ctx,
                    row,
                    "miniatura_not_a_post",
                    f'no se programó: el "Tipo" es {row.content_type}, que es una miniatura '
                    "y no un post. Si sí es un post, cambia el Tipo en Notion.",
                )
                continue
            file_ref = classify_final_file(row.final_file_url)
            if file_ref.kind in {FinalFileKind.EMPTY, FinalFileKind.INVALID}:
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: Archivo Final is not an http(s) URL (%s)",
                    row.page_id,
                    (file_ref.raw_url or "empty")[:120],
                )
                page_url = getattr(row, "url", None) or row.page_id
                _alert(
                    ctx,
                    row,
                    "invalid_archivo_final",
                    invalid_archivo_final_message(page_url, row.final_file_url),
                )
                continue
            if (
                not dry
                and not completing_split
                and guard.contains(settings.metricool_blog_id, row.page_id)
            ):
                stats["skipped"] += 1
                logger.warning(
                    "Skip %s: prior schedule attempt; reconcile before retry", row.page_id
                )
                _alert(
                    ctx,
                    row,
                    "prior_attempt",
                    "no se programó: ya hubo un intento anterior de programarla que no "
                    "terminó bien, y para no duplicarla no se reintenta sola. Revisa en "
                    "Metricool si el post existe: si existe, pasa la pieza a Programado en "
                    "Notion; si no existe, pídele a Kap que libere el bloqueo.",
                )
                continue
            if cap is not None and stats["scheduled"] >= cap:
                logger.info(
                    "SCHEDULE_MAX_PER_RUN=%s reached; leaving remaining rows",
                    cap,
                )
                break

            hook = (getattr(row, "cover_text", "") or "").strip()
            if "youtube" in networks and not hook:
                stats["skipped"] += 1
                logger.warning("Skip %s: YouTube row has no Titulo hook", row.page_id)
                _alert(
                    ctx,
                    row,
                    "missing_hook",
                    'no se programó: va a YouTube y falta "Titulo" (es el título del video '
                    'en YouTube). Llena "Titulo" en Notion.',
                )
                continue

            cover = resolve_instagram_reel_cover(
                settings=settings,
                row=row,
                networks=networks,
                metricool=metricool,
                dry_run=dry,
            )
            if cover.skip_reason:
                stats["skipped"] += 1
                logger.warning("Skip %s: %s", row.page_id, cover.skip_reason)
                _alert(
                    ctx,
                    row,
                    cover.skip_reason,
                    "no se programó: problema con la portada del Reel "
                    f'("Miniatura" o "Archivo Final"). Detalle: '
                    f"{cover.skip_message or cover.skip_reason}",
                )
                continue

            youtube_existing = file_ref.kind == FinalFileKind.YOUTUBE
            media_urls: list[str] = []
            if row.final_file_url:
                try:
                    media_urls = prepare_media_urls_for_metricool(
                        settings=settings,
                        metricool=metricool,
                        raw_archivo_final=row.final_file_url,
                        content_type=row.content_type,
                        dry_run=dry,
                        notion_page_id=row.page_id,
                        publication=pub,
                        networks=networks,
                    )
                except MediaHostError as exc:
                    stats["skipped"] += 1
                    logger.warning("Skip %s: media_host_failed: %s", row.page_id, exc)
                    _alert(
                        ctx,
                        row,
                        "media_host_failed",
                        'no se programó: no pude descargar o subir el archivo de "Archivo '
                        'Final" (revisa que el link sea público o que la carpeta esté '
                        f"compartida). Detalle: {exc}",
                    )
                    continue

            if not media_urls:
                stats["skipped"] += 1
                logger.warning("Skip %s: media resolved to nothing", row.page_id)
                _alert(
                    ctx,
                    row,
                    "missing_media",
                    'no se programó: no encontré ningún archivo en "Archivo Final" '
                    "(carpeta vacía o sin videos/imágenes). Revisa el link en Notion.",
                )
                continue
            kept, dropped_for_images = partition_image_networks(networks, media_urls)
            if dropped_for_images:
                logger.info(
                    "Drop networks for %s (image-only media): dropped=%s "
                    "posting=%s canal=%r labels=%s",
                    row.page_id,
                    dropped_for_images,
                    kept,
                    row.channel,
                    labels,
                )
            if not kept:
                stats["skipped"] += 1
                reason = video_network_skip_reason(networks, media_urls) or "no_video_for_network"
                logger.warning(
                    "Skip %s: %s for %s (media extensions are images)",
                    row.page_id,
                    reason,
                    networks,
                )
                _alert(
                    ctx,
                    row,
                    reason,
                    f'no se programó: "Canal" pide {", ".join(networks)} pero "Archivo '
                    'Final" solo tiene imágenes, y esas redes necesitan video. Sube el '
                    'video a "Archivo Final" o cambia el Canal.',
                )
                continue
            networks = kept
            logger.info(
                "Schedule %s canal=%r labels=%s networks=%s",
                row.page_id,
                row.channel,
                labels,
                networks,
            )

            body = build_schedule_body(
                caption=caption,
                publication=pub,
                tz_name=settings.timezone,
                channel=row.channel,
                title=row.title,
                content_type=row.content_type,
                media_urls=media_urls,
                cover_url=cover.cover_url,
                youtube_existing_video=youtube_existing,
                networks=networks,
                youtube_short=plan.youtube_short,
                youtube_title=hook,
            )
            # videoThumbnailUrl is shared by every provider. Instagram keeps the
            # reel cover; YouTube is a second post with no thumbnail fields.
            bodies = schedule_bodies(body)
            if len(bodies) > 1:
                logger.info(
                    "Split %s: reel cover stays on %s; YouTube is a separate post "
                    "without a thumbnail",
                    row.page_id,
                    [item.get("network") for item in bodies[0].get("providers") or []],
                )
            if dry:
                if len(bodies) == 1:
                    logger.info("[dry-run] Would schedule Metricool post for %s", row.page_id)
                else:
                    logger.info(
                        "[dry-run] Would schedule %s Metricool posts for %s",
                        len(bodies),
                        row.page_id,
                    )
            else:
                # Reserve BEFORE POST: timeout/Notion failure must never auto-resend.
                # Completing a split reuses the reservation from the half that
                # already exists, and creates only the missing network.
                reserved = guard.reserve(settings.metricool_blog_id, row.page_id)
                if not reserved and not completing_split:
                    stats["skipped"] += 1
                    continue
                responses = [metricool.create_scheduled_post(part) for part in bodies]
                created_ids, created_uuids = _created_refs(responses)
                logger.info(
                    "Created Metricool post(s) for %s ids=%s uuids=%s",
                    row.page_id,
                    created_ids or "-",
                    created_uuids or "-",
                )
                # Status flips only after every create returns. A failed create
                # raises above and leaves Notion on Aprobado.
                notion.set_status(row.page_id, settings.notion_status_scheduled, dry_run=False)
                _store_refs(notion, row, created_ids, created_uuids, dry)
            stats["scheduled"] += 1
            taken_slots.append(
                {
                    "page_id": row.page_id,
                    "url": getattr(row, "url", None) or row.page_id,
                    "publication": pub,
                    "groups": publication_format_groups(
                        networks,
                        title=row.title,
                        content_type=row.content_type,
                    ),
                }
            )
            _alert(
                ctx,
                row,
                "scheduled",
                f"programada para {_fmt_local(pub)} en {', '.join(networks)}.",
            )
        except Exception as exc:
            stats["errors"] += 1
            logger.exception("Schedule failed for %s: %s", row.page_id, exc)
            _alert(
                ctx,
                row,
                "schedule_error",
                f"error al programar en Metricool; Notion sigue en Aprobado. Detalle: {exc}",
            )

    metricool.close()
    if not dry:
        digest.flush()
    return stats
