"""Extract typed values from Notion page properties."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


def _prop(props: dict[str, Any], name: str) -> dict[str, Any] | None:
    p = props.get(name)
    return p if isinstance(p, dict) else None


def read_title(props: dict[str, Any], name: str) -> str:
    p = _prop(props, name)
    if not p or p.get("type") != "title":
        return ""
    parts = p.get("title") or []
    return "".join(part.get("plain_text", "") for part in parts).strip()


def read_rich_text(props: dict[str, Any], name: str) -> str:
    p = _prop(props, name)
    if not p:
        return ""
    t = p.get("type")
    if t == "rich_text":
        parts = p.get("rich_text") or []
        return "".join(part.get("plain_text", "") for part in parts).strip()
    if t == "title":
        return read_title(props, name)
    return ""


def read_select(props: dict[str, Any], name: str) -> str | None:
    p = _prop(props, name)
    if not p:
        return None
    t = p.get("type")
    if t == "select":
        sel = p.get("select")
        return sel.get("name") if sel else None
    if t == "status":
        st = p.get("status")
        return st.get("name") if st else None
    return None


def read_multi_select(props: dict[str, Any], name: str) -> list[str]:
    p = _prop(props, name)
    if not p or p.get("type") != "multi_select":
        return []
    return [x.get("name", "") for x in (p.get("multi_select") or []) if x.get("name")]


def read_protagonistas_label(props: dict[str, Any], name: str) -> str:
    """Join Notion multi_select protagonistas for cover-agent task dict."""
    return ", ".join(read_multi_select(props, name))


def read_url(props: dict[str, Any], name: str) -> str | None:
    p = _prop(props, name)
    if not p:
        return None
    if p.get("type") == "url":
        return p.get("url")
    if p.get("type") == "files":
        files = p.get("files") or []
        for f in files:
            if f.get("type") == "external":
                return f.get("external", {}).get("url")
            if f.get("type") == "file":
                return f.get("file", {}).get("url")
    return None


def read_date(props: dict[str, Any], name: str) -> date | datetime | None:
    p = _prop(props, name)
    if not p or p.get("type") != "date":
        return None
    d = p.get("date")
    if not d or not d.get("start"):
        return None
    start = d["start"]
    if "T" in start:
        # Notion returns ISO; keep as datetime if time present
        return datetime.fromisoformat(start.replace("Z", "+00:00"))
    return date.fromisoformat(start)


class NotionPostRow:
    """Normalized view of a Parrilla content row."""

    def __init__(
        self,
        *,
        page_id: str,
        url: str,
        status: str | None,
        publication: date | datetime | None,
        channel: str | None,
        caption: str,
        final_file_url: str | None,
        title: str,
        content_type: str | None,
        miniatura_url: str | None,
        protagonistas: str,
        metricool_id: str | None = None,
        metricool_uuid: str | None = None,
    ) -> None:
        self.page_id = page_id
        self.url = url
        self.status = status
        self.publication = publication
        self.channel = channel
        self.caption = caption
        self.final_file_url = final_file_url
        self.title = title
        self.content_type = content_type
        self.miniatura_url = miniatura_url
        self.protagonistas = protagonistas
        self.metricool_id = metricool_id
        self.metricool_uuid = metricool_uuid


def _without_linkedin(names: list[str]) -> list[str]:
    return [name for name in names if "linkedin" not in name.casefold()]


def _first_channel(props: dict[str, Any], name: str) -> str | None:
    multi = read_multi_select(props, name)
    if len(multi) > 1:
        kept = _without_linkedin(multi)
        if len(kept) == 1:
            # Instagram + LinkedIn schedules the non-LinkedIn network only.
            return kept[0]
        if not kept:
            return multi[0]
        # Several non-LinkedIn networks: do not pick one silently.
        return None
    if multi:
        return multi[0]
    return read_select(props, name)


def read_metricool_ref(props: dict[str, Any], name: str) -> str | None:
    """Optional stored Metricool id/uuid (rich text, number, or url)."""
    if not name:
        return None
    text = read_rich_text(props, name)
    if text:
        return text
    p = _prop(props, name)
    if not p:
        return None
    if p.get("type") == "number" and p.get("number") is not None:
        number = p["number"]
        if isinstance(number, float) and number.is_integer():
            return str(int(number))
        return str(number)
    if p.get("type") == "url" and p.get("url"):
        return str(p["url"])
    return None


def _first_or_rich_title(props: dict[str, Any], name: str) -> str:
    t = read_title(props, name)
    if t:
        return t
    return read_rich_text(props, name)


def _first_content_type(props: dict[str, Any], name: str) -> str | None:
    multi = read_multi_select(props, name)
    if multi:
        return multi[0]
    return read_select(props, name)


def row_from_page(page: dict[str, Any], settings_names: dict[str, str]) -> NotionPostRow:
    props = page.get("properties") or {}
    caption = read_rich_text(props, settings_names["caption"])
    return NotionPostRow(
        page_id=page["id"],
        url=page.get("url", ""),
        status=read_select(props, settings_names["status"]),
        publication=read_date(props, settings_names["publication"]),
        channel=_first_channel(props, settings_names["channel"]),
        caption=caption,
        final_file_url=read_url(props, settings_names["final_file"]),
        title=_first_or_rich_title(props, settings_names["title"]),
        content_type=_first_content_type(props, settings_names["content_type"]),
        miniatura_url=read_url(props, settings_names["miniatura"]),
        protagonistas=read_protagonistas_label(props, settings_names["protagonista"]),
        metricool_id=read_metricool_ref(props, settings_names.get("metricool_id", "")),
        metricool_uuid=read_metricool_ref(props, settings_names.get("metricool_uuid", "")),
    )
