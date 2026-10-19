"""Read a Título + caption block from a Notion page body.

The Parrilla piece «Los directivos perdieron la esperanza en la Generación Z»
leaves Titulo and Caption empty and puts the publishable copy in the body::

    Título:

    LA GENERACIÓN Z NO ES EL PROBLEMA

    ¿La generación Z está dañando el mercado laboral...

    #liderazgo

A confident read of that shape can be copied into the empty properties.
Anything less clear is only a proposal for a person to confirm.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from metricool_sync_posts.cover.reel import instagram_reel
from metricool_sync_posts.jobs.common import caption_has_placeholder
from metricool_sync_posts.jobs.content_types import is_story_type

logger = logging.getLogger(__name__)

# The label is its own line. "Título de la publicación:" is a different field.
_TITULO_LINE = re.compile(r"^t[ií]tulo\s*:\s*(.*)$", re.IGNORECASE)
_CAPTION_LINE = re.compile(r"^caption\s*:\s*(.*)$", re.IGNORECASE)
_NOTE_LINE = re.compile(
    r"^(?:fuentes?|nota(?:\s+para\s+edici[oó]n)?|gui[oó]n)\s*:",
    re.IGNORECASE,
)
_HASHTAG_LINE = re.compile(r"^(?:#[\wÁÉÍÓÚáéíóúñÑÜü]+[\s.]*)+$")
_NOISE_LINE = re.compile(r"^[-—*_]{3,}$")
_LETTERS = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü]")

# A cover/YouTube title is a short line. A wrapped sentence is a caption.
_MAX_TITLE_CHARS = 100
_MAX_TITLE_WORDS = 14
_MIN_CAPTION_LETTERS = 30


@dataclass(frozen=True)
class BodyCopy:
    """A Título: block found in the page body.

    ``confident`` is true only when both the title line and the caption under
    it are unambiguous. Otherwise the fields are a proposal, not a write.
    """

    titulo: str | None
    caption: str | None
    confident: bool


@dataclass(frozen=True)
class RowCopy:
    """Caption and Titulo the schedule job should use for this row."""

    caption: str
    titulo: str
    skip_reason: str | None = None
    skip_message: str | None = None


def parse_body_copy(text: str) -> BodyCopy | None:
    """Parse a «Título:» line and the caption paragraphs under it.

    Returns None when the body has no such line. Script or notes placed
    above the line are ignored. A second «Título:» makes the read uncertain
    and the proposal is the last block.
    """
    if not text or not text.strip():
        return None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in normalized.split("\n")]
    hits: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _TITULO_LINE.match(line)
        if match:
            hits.append((index, match.group(1).strip()))
    if not hits:
        return None

    index, inline = hits[-1]
    if inline:
        title = _clean_title(inline)
        title_at = index
    else:
        title = ""
        title_at = index
        for cursor in range(index + 1, len(lines)):
            if lines[cursor]:
                title = _clean_title(lines[cursor])
                title_at = cursor
                break

    caption_lines: list[str] = []
    for line in lines[title_at + 1 :]:
        if not line or _NOISE_LINE.match(line):
            continue
        if _NOTE_LINE.match(line):
            break
        caption_lines.append(line)
    if caption_lines:
        labeled = _CAPTION_LINE.match(caption_lines[0])
        if labeled:
            rest = labeled.group(1).strip()
            caption_lines = ([rest] if rest else []) + caption_lines[1:]
    caption = "\n\n".join(caption_lines).strip()
    confident = len(hits) == 1 and _title_ok(title) and _caption_ok(caption)
    return BodyCopy(
        titulo=title or None,
        caption=caption or None,
        confident=confident,
    )


def confirm_message(parsed: BodyCopy) -> str:
    """Spanish Slack text with the proposed Titulo and Caption."""
    titulo = _clip(parsed.titulo) if parsed.titulo else "(no está claro)"
    caption = _clip(parsed.caption) if parsed.caption else "(no está claro)"
    return (
        "no se programó: en el cuerpo de la página hay un posible «Título:» "
        "y un párrafo de caption, pero no los copié a las propiedades porque "
        "no estoy seguro. Confírmalos en «Titulo» y «Caption».\n"
        f"Título propuesto: «{titulo}»\n"
        f"Caption propuesto: «{caption}»"
    )


def resolve_row_copy(
    notion,
    row,
    *,
    networks: list[str],
    dry_run: bool,
    caption_property: str,
    titulo_property: str,
    require_cover: bool,
) -> RowCopy:
    """Fill empty Titulo/Caption from a confident body block, or ask.

    Non-empty properties are never overwritten. Instagram Stories do not
    need either field: an uncertain body does not skip them.
    """
    caption_prop = (getattr(row, "caption", "") or "").strip()
    titulo_prop = (getattr(row, "cover_text", "") or "").strip()
    if caption_prop and titulo_prop:
        return RowCopy(caption=caption_prop, titulo=titulo_prop)

    body = _read_body(notion, getattr(row, "page_id", ""))
    parsed = parse_body_copy(body) if body else None
    story = is_story_type(getattr(row, "content_type", None))

    if parsed is not None and parsed.confident:
        caption = caption_prop or (parsed.caption or "")
        titulo = titulo_prop or (parsed.titulo or "")
        if not caption_has_placeholder(caption):
            _apply_confident(
                notion,
                row,
                parsed,
                caption_prop=caption_prop,
                titulo_prop=titulo_prop,
                caption_property=caption_property,
                titulo_property=titulo_property,
                dry_run=dry_run,
            )
        return RowCopy(caption=caption, titulo=titulo)

    if parsed is not None:
        needs_caption = not caption_prop and not story
        needs_titulo = not titulo_prop and _titulo_required(
            networks, row, require_cover=require_cover
        )
        if needs_caption or needs_titulo:
            return RowCopy(
                caption=caption_prop,
                titulo=titulo_prop,
                skip_reason="copy_needs_confirm",
                skip_message=confirm_message(parsed),
            )
        return RowCopy(caption=caption_prop, titulo=titulo_prop)

    return RowCopy(caption=caption_prop or body, titulo=titulo_prop)


def _apply_confident(
    notion,
    row,
    parsed: BodyCopy,
    *,
    caption_prop: str,
    titulo_prop: str,
    caption_property: str,
    titulo_property: str,
    dry_run: bool,
) -> None:
    updates: dict[str, str] = {}
    if not caption_prop and parsed.caption and caption_property:
        updates[caption_property] = parsed.caption
        row.caption = parsed.caption
    if not titulo_prop and parsed.titulo and titulo_property:
        updates[titulo_property] = parsed.titulo
        row.cover_text = parsed.titulo
    if not updates:
        return
    logger.info(
        "Auto-fill %s on %s from the page body",
        ", ".join(updates),
        getattr(row, "page_id", ""),
    )
    setter = getattr(notion, "set_rich_text_properties", None)
    if not callable(setter):
        return
    try:
        setter(row.page_id, updates, dry_run=dry_run)
    except Exception:
        logger.exception("Could not write Titulo/Caption on %s", row.page_id)


def _titulo_required(networks: list[str], row, *, require_cover: bool) -> bool:
    if is_story_type(getattr(row, "content_type", None)):
        return False
    lowered = {str(network).strip().lower() for network in networks}
    if "youtube" in lowered:
        return True
    if not require_cover:
        return False
    return instagram_reel(
        list(networks),
        getattr(row, "title", "") or "",
        getattr(row, "content_type", None),
    )


def _read_body(notion, page_id: str) -> str:
    getter = getattr(notion, "page_body_plain_text", None)
    if not callable(getter) or not page_id:
        return ""
    try:
        raw = getter(page_id)
    except Exception:
        logger.exception("Could not read Notion body for %s", page_id)
        return ""
    return raw.strip() if isinstance(raw, str) else ""


def _clean_line(line: str) -> str:
    text = line.replace("\u00a0", " ").strip()
    text = re.sub(r"^#{1,6}\s+", "", text)
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_line(value)).strip()


def _title_ok(title: str) -> bool:
    if not title or _HASHTAG_LINE.match(title) or _TITULO_LINE.match(title):
        return False
    if len(title) > _MAX_TITLE_CHARS:
        return False
    words = title.split()
    if len(words) < 2 or len(words) > _MAX_TITLE_WORDS:
        return False
    return True


def _caption_ok(caption: str) -> bool:
    prose = [
        line
        for line in caption.splitlines()
        if line.strip() and not _HASHTAG_LINE.match(line.strip())
    ]
    return len(_LETTERS.findall("\n".join(prose))) >= _MIN_CAPTION_LETTERS


def _clip(text: str, limit: int = 400) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
