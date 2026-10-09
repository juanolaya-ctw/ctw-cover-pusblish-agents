"""Pure matching helpers between Notion rows and Metricool scheduled posts."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from metricool_sync_posts.metricool.channels import normalize_channel, plan_canals
from metricool_sync_posts.timeutil import dates_equal_within_minutes, notion_date_to_datetime

# Same network and publication time this close is the same slot, even if the caption changed.
SLOT_MATCH_MINUTES = 15

_TOKEN_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "ante",
        "con",
        "de",
        "del",
        "el",
        "ella",
        "en",
        "es",
        "esa",
        "ese",
        "eso",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "para",
        "por",
        "que",
        "se",
        "su",
        "sus",
        "un",
        "una",
        "uno",
        "y",
        "ya",
        "the",
        "and",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "this",
        "that",
    }
)

# Provider statuses from Metricool swagger ProviderStatus.
_LEAVE_STATUSES = frozenset(
    {
        "PENDING",
        "PUBLISHING",
        "AWAITING_CONFIRMATION",
        "DRAFT",
        "SCHEDULED",
    }
)


def normalize_text(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def post_publication_datetime(post: dict[str, Any], tz_name: str) -> datetime | None:
    pd = post.get("publicationDate") or post.get("publication_date")
    if not isinstance(pd, dict):
        return None
    dt_str = pd.get("dateTime") or pd.get("datetime")
    zone = pd.get("timezone") or tz_name
    if not dt_str:
        return None
    naive = datetime.fromisoformat(str(dt_str))
    return notion_date_to_datetime(naive, zone)


def _data_block_active(block: dict[str, Any]) -> bool:
    """True when a *Data object carries a real payload, not an empty stub."""
    for value in block.values():
        if value is None or value is False or value == "" or value == [] or value == {}:
            continue
        return True
    return False


def post_networks(post: dict[str, Any]) -> set[str]:
    """Networks this post actually targets.

    Prefer ``providers[].network``. Metricool includes ``twitterData`` on posts
    that are not on Twitter; counting every ``*Data`` key marks those as twitter.
    Empty data blocks are ignored when providers are absent.
    """
    nets: set[str] = set()
    for provider in post.get("providers") or []:
        if isinstance(provider, dict) and provider.get("network"):
            nets.add(str(provider["network"]).lower())
    if nets:
        return nets
    for key, value in post.items():
        if not key.endswith("Data") or not isinstance(value, dict):
            continue
        if not _data_block_active(value):
            continue
        base = key[: -len("Data")].lower()
        if base:
            nets.add(base)
    return nets


def _provider_statuses(post: dict[str, Any]) -> list[str]:
    statuses: list[str] = []
    for provider in post.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        raw = provider.get("status")
        if isinstance(raw, str) and raw.strip():
            statuses.append(raw.strip().upper())
    return statuses


def post_state(post: dict[str, Any]) -> str:
    """Return PUBLISHED, ERROR, PENDING, SCHEDULED, or UNKNOWN.

    Live payloads put status on each provider (PUBLISHED / PENDING / ERROR / …),
    not on the post. Top-level ``state``/``status`` is only a fallback for
    fixtures that predate that shape.
    """
    statuses = _provider_statuses(post)
    if statuses:
        if any("ERROR" in status or "FAIL" in status for status in statuses):
            return "ERROR"
        if all(status == "PUBLISHED" for status in statuses):
            return "PUBLISHED"
        if any(
            status in _LEAVE_STATUSES or "PEND" in status or "SCHED" in status
            for status in statuses
        ):
            return "PENDING"
        return "UNKNOWN"

    for field in ("state", "status", "publicationStatus", "postStatus"):
        val = post.get(field)
        if isinstance(val, str):
            upper = val.upper()
            if "ERROR" in upper or "FAIL" in upper:
                return "ERROR"
            if "PUBLISH" in upper and "UN" not in upper:
                return "PUBLISHED"
            if "PEND" in upper:
                return "PENDING"
            if "SCHED" in upper:
                return "SCHEDULED"
    published = post.get("published") or post.get("isPublished")
    if published is True:
        return "PUBLISHED"
    if published is False:
        return "SCHEDULED"
    return "UNKNOWN"


def _post_text_fields(post: dict[str, Any]) -> list[str]:
    fields = [str(post.get("text") or "")]
    youtube = post.get("youtubeData")
    if isinstance(youtube, dict):
        fields.append(str(youtube.get("title") or ""))
    return fields


def content_tokens(text: str) -> set[str]:
    """Words that can identify a piece. Drops short words and Spanish/English stopwords."""
    folded = normalize_text(text)
    decomposed = unicodedata.normalize("NFD", folded)
    folded = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    words = folded.split()
    return {word for word in words if len(word) >= 4 and word not in _TOKEN_STOPWORDS}


def token_overlap_similar(left: str, right: str) -> bool:
    """True when edited captions still share enough content words."""
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = left_tokens & right_tokens
    if len(shared) >= 3:
        return True
    smaller = min(len(left_tokens), len(right_tokens))
    return len(shared) >= 2 and len(shared) / smaller >= 0.34


def texts_similar(left: str, right: str) -> bool:
    """Caption/title similarity that does not depend on the clock time."""
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 20 and shorter in longer:
        return True
    prefix = min(60, len(a), len(b))
    return prefix >= 40 and a[:prefix] == b[:prefix]


def text_similarity_score(caption: str, title: str | None, post: dict[str, Any]) -> float:
    """Higher is better. 0 means the copy does not identify this post."""
    best = 0.0
    caption_norm = normalize_text(caption)
    title_norm = normalize_text(title or "")
    for raw in _post_text_fields(post):
        other = normalize_text(raw)
        if not other:
            continue
        if caption_norm and caption_norm == other:
            best = max(best, 10.0)
        elif caption_norm and texts_similar(caption_norm, other):
            best = max(best, 7.0)
        if title_norm and title_norm == other:
            best = max(best, 8.0)
        elif title_norm and texts_similar(title_norm, other):
            best = max(best, 6.0)
    return best


def _wanted_networks(notion_channel: str | None) -> set[str]:
    if not notion_channel:
        return set()
    parts = [part.strip() for part in notion_channel.split(",") if part.strip()]
    plan = plan_canals(parts)
    if plan.networks:
        return set(plan.networks)
    if any(normalize_channel(part) == "linkedin" for part in parts):
        return {"linkedin"}
    return set()


def _network_compatible(post: dict[str, Any], notion_channel: str | None) -> bool:
    wanted = _wanted_networks(notion_channel)
    if not wanted:
        return True
    networks = post_networks(post)
    if networks and not (wanted & networks):
        return False
    return True


def _ids_match(post: dict[str, Any], metricool_id: str | None, metricool_uuid: str | None) -> bool:
    post_id = str(post.get("id") or post.get("postId") or "")
    post_uuid = str(post.get("uuid") or "")
    if metricool_id and post_id and post_id == str(metricool_id).strip():
        return True
    if metricool_uuid and post_uuid and post_uuid == str(metricool_uuid).strip():
        return True
    return False


def _within_days(
    post: dict[str, Any],
    notion_publication: datetime | None,
    tz_name: str,
    days: int,
) -> bool:
    if notion_publication is None:
        return True
    mc_date = post_publication_datetime(post, tz_name)
    if mc_date is None:
        return False
    return abs(mc_date - notion_publication) <= timedelta(days=days)


def match_score(
    *,
    notion_caption: str,
    notion_channel: str | None,
    notion_publication: datetime,
    metricool_post: dict[str, Any],
    tz_name: str,
) -> float:
    """Higher is better; 0 means no match.

    Time gate is ±5 minutes. Date changes (sync_dates) use ``find_sync_match``.
    """
    mc_date = post_publication_datetime(metricool_post, tz_name)
    if mc_date is None:
        return 0.0
    if not dates_equal_within_minutes(notion_publication, mc_date, minutes=5):
        return 0.0
    if not _network_compatible(metricool_post, notion_channel):
        return 0.0
    mc_text = normalize_text(str(metricool_post.get("text") or ""))
    no_text = normalize_text(notion_caption)
    if mc_text and no_text:
        if mc_text == no_text:
            return 10.0
        if mc_text in no_text or no_text in mc_text:
            return 7.0
        if mc_text[:40] == no_text[:40]:
            return 5.0
        return 0.0
    return 3.0


def find_best_match(
    notion_caption: str,
    notion_channel: str | None,
    notion_publication: datetime,
    candidates: list[dict[str, Any]],
    tz_name: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for post in candidates:
        score = match_score(
            notion_caption=notion_caption,
            notion_channel=notion_channel,
            notion_publication=notion_publication,
            metricool_post=post,
            tz_name=tz_name,
        )
        if score > best_score:
            best_score = score
            best = post
    return best if best_score > 0 else None


def find_sync_match(
    *,
    notion_caption: str,
    notion_title: str | None,
    notion_channel: str | None,
    notion_publication: datetime | None,
    candidates: list[dict[str, Any]],
    tz_name: str,
    metricool_id: str | None = None,
    metricool_uuid: str | None = None,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """Match a Notion row to a Metricool post without requiring the same clock time.

    Stored Metricool id/uuid wins. Otherwise caption or title similarity inside
    ``window_days`` of the Notion publication. Notion remains the date source.
    """
    if metricool_id or metricool_uuid:
        for post in candidates:
            if _ids_match(post, metricool_id, metricool_uuid):
                return post

    best: dict[str, Any] | None = None
    best_score = 0.0
    best_delta: float | None = None
    for post in candidates:
        if not _within_days(post, notion_publication, tz_name, window_days):
            continue
        if not _network_compatible(post, notion_channel):
            continue
        score = text_similarity_score(notion_caption, notion_title, post)
        if score <= 0:
            continue
        mc_date = post_publication_datetime(post, tz_name)
        delta = (
            abs((mc_date - notion_publication).total_seconds())
            if mc_date is not None and notion_publication is not None
            else 0.0
        )
        if score > best_score or (
            score == best_score and (best_delta is None or delta < best_delta)
        ):
            best = post
            best_score = score
            best_delta = delta
    return best


def _media_values(post: dict[str, Any]) -> list[str]:
    media = post.get("media")
    if isinstance(media, str):
        return [media]
    if isinstance(media, list):
        return [str(item) for item in media if isinstance(item, str) and item.strip()]
    return []


def media_overlaps(notion_urls: list[str] | None, post: dict[str, Any]) -> bool:
    if not notion_urls:
        return False
    haystack = " ".join(_media_values(post)).lower()
    if not haystack.strip():
        return False
    for url in notion_urls:
        token = (url or "").strip().lower()
        if len(token) >= 12 and token in haystack:
            return True
    return False


def _wanted_from_args(network: str | None, networks: list[str] | None) -> set[str]:
    if networks:
        return {str(item).strip().lower() for item in networks if str(item).strip()}
    if network:
        return {network.strip().lower()}
    return set()


def _same_publication_slot(
    post: dict[str, Any],
    notion_publication: datetime | None,
    tz_name: str,
    wanted: set[str],
    *,
    minutes: int = SLOT_MATCH_MINUTES,
) -> bool:
    """Same network(s) and the same publication time, within ``minutes``."""
    if notion_publication is None:
        return False
    mc_date = post_publication_datetime(post, tz_name)
    if mc_date is None or not dates_equal_within_minutes(notion_publication, mc_date, minutes):
        return False
    post_nets = post_networks(post)
    if wanted and post_nets and not (wanted & post_nets):
        return False
    if wanted and not post_nets:
        return False
    return True


def _duplicate_score(
    post: dict[str, Any],
    *,
    caption: str,
    title: str | None,
    wanted: set[str],
    tz_name: str,
    notion_publication: datetime | None,
    media_urls: list[str] | None,
    window_days: int,
) -> float:
    if not _within_days(post, notion_publication, tz_name, window_days):
        return 0.0
    nets = post_networks(post)
    if wanted and nets and not (wanted & nets):
        return 0.0
    score = text_similarity_score(caption, title, post)
    for source in (caption, title or ""):
        for field in _post_text_fields(post):
            if token_overlap_similar(source, field):
                score = max(score, 6.0)
    if score <= 0 and media_overlaps(media_urls, post):
        score = 4.0
    if _same_publication_slot(post, notion_publication, tz_name, wanted):
        # Edited caption, same slot: still the same piece. Prefer skip over a second post.
        score = max(score, 9.0)
    return score


def find_duplicate_candidates(
    candidates: list[dict[str, Any]],
    *,
    caption: str,
    title: str | None,
    network: str | None,
    tz_name: str,
    notion_publication: datetime | None = None,
    media_urls: list[str] | None = None,
    window_days: int = 7,
    networks: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Every Metricool post that could already be this piece.

    More than one hit is ambiguous: the caller should skip and report, not create.
    """
    wanted = _wanted_from_args(network, networks)
    hits: list[dict[str, Any]] = []
    for post in candidates:
        score = _duplicate_score(
            post,
            caption=caption,
            title=title,
            wanted=wanted,
            tz_name=tz_name,
            notion_publication=notion_publication,
            media_urls=media_urls,
            window_days=window_days,
        )
        if score > 0:
            hits.append(post)
    return hits


def find_existing_piece(
    candidates: list[dict[str, Any]],
    *,
    caption: str,
    title: str | None,
    network: str | None,
    tz_name: str,
    notion_publication: datetime | None = None,
    media_urls: list[str] | None = None,
    window_days: int = 7,
    networks: list[str] | None = None,
) -> dict[str, Any] | None:
    """Find a Metricool post that is already this piece, at any time in the window."""
    wanted = _wanted_from_args(network, networks)
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_delta: float | None = None
    for post in candidates:
        score = _duplicate_score(
            post,
            caption=caption,
            title=title,
            wanted=wanted,
            tz_name=tz_name,
            notion_publication=notion_publication,
            media_urls=media_urls,
            window_days=window_days,
        )
        if score <= 0:
            continue
        mc_date = post_publication_datetime(post, tz_name)
        delta = (
            abs((mc_date - notion_publication).total_seconds())
            if mc_date is not None and notion_publication is not None
            else 0.0
        )
        if score > best_score or (
            score == best_score and (best_delta is None or delta < best_delta)
        ):
            best = post
            best_score = score
            best_delta = delta
    return best
