"""Map Notion Canal values to Metricool provider network names."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

NOTION_TO_METRICOOL: dict[str, str] = {
    "instagram": "instagram",
    "ig": "instagram",
    "facebook": "facebook",
    "fb": "facebook",
    "linkedin": "linkedin",
    "twitter": "twitter",
    "x": "twitter",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "yt": "youtube",
    "pinterest": "pinterest",
    "threads": "threads",
}


# Colombia Tech's usual connected set when simpleProfiles cannot be read.
FALLBACK_CONNECTED_NETWORKS = frozenset({"instagram", "tiktok", "youtube"})
# LinkedIn may be connected on the brand. This pipeline still does not publish it.
NEVER_SCHEDULE_NETWORKS = frozenset({"linkedin"})

# Exact Canal labels. These do not go through token splitting.
_EXPLICIT_CANAL_NETWORKS = {
    "canal ig": "instagram",
    "canalig": "instagram",
}


def explicit_canal_network(label: str | None) -> str | None:
    """Map a whole Canal label. ``Canal Ig`` is the colombiatechoficial feed."""
    if not label:
        return None
    folded = _fold_label(label)
    compact = _compact_label(label)
    if folded in _EXPLICIT_CANAL_NETWORKS:
        return _EXPLICIT_CANAL_NETWORKS[folded]
    if compact in _EXPLICIT_CANAL_NETWORKS:
        return _EXPLICIT_CANAL_NETWORKS[compact]
    return None


def normalize_channel(notion_channel: str | None, *, title: str | None = None) -> str | None:
    """Map a Canal label to one Metricool network.

    ``Canal Ig`` is Instagram by exact label, not because the token ``ig``
    appears in the text. LinkedIn is removed when another network is present
    (multi-network rows keep the non-LinkedIn destination). A LinkedIn-only
    label still returns ``linkedin`` so callers can exclude it. ``title`` is
    accepted for caller compatibility and does not change the network.
    """
    _ = title
    if not notion_channel:
        return None
    explicit = explicit_canal_network(notion_channel)
    if explicit:
        return explicit
    key = notion_channel.strip().lower()
    tokens = key.replace("/", " ").replace("+", " ").replace(",", " ").split()
    networks = {
        NOTION_TO_METRICOOL[token]
        for token in tokens
        if token in NOTION_TO_METRICOOL
    }
    if "linkedin" in networks and len(networks) > 1:
        networks.discard("linkedin")
    if len(networks) == 1:
        return next(iter(networks))
    return None


def _fold_label(label: str) -> str:
    raw = re.sub(r"[\s\u00a0]+", " ", label).strip().casefold()
    decomposed = unicodedata.normalize("NFD", raw)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _compact_label(label: str) -> str:
    return re.sub(r"[\s_-]+", "", _fold_label(label))


def label_is_youtube_short(label: str) -> bool:
    """True for Canal values like 'Youtube Shorts' / 'YT Shorts' (any casing)."""
    folded = _fold_label(label)
    compact = _compact_label(label)
    if compact in {"youtubeshorts", "youtubeshort", "ytshorts", "ytshort"}:
        return True
    if re.search(r"\bshorts?\b", folded) and (
        "youtube" in folded or re.search(r"\byt\b", folded)
    ):
        return True
    return False


def _is_ig_nico(label: str) -> bool:
    folded = _fold_label(label)
    if folded == "ig nico" or _compact_label(label) == "ignico":
        return True
    # A joined display string still skips the row when IG Nico is one of the values.
    return re.search(r"\big nico\b", folded) is not None


def _is_newsletter(label: str) -> bool:
    return _fold_label(label) == "newsletter"


def _is_linkedin_only(label: str) -> bool:
    folded = _fold_label(label)
    if "linkedin" not in folded:
        return False
    network = normalize_channel(label)
    return network in (None, "linkedin")


def _excluded_label(label: str, exclude: frozenset[str]) -> bool:
    if not exclude:
        return False
    folded = _fold_label(label)
    return folded in {_fold_label(item) for item in exclude if item.strip()}


def canal_labels(row: object) -> list[str]:
    """Canal names on a Notion row. Multi-select wins over a joined display string."""
    raw = getattr(row, "channels", None)
    if raw:
        return [str(item).strip() for item in raw if str(item).strip()]
    channel = getattr(row, "channel", None)
    if channel and str(channel).strip():
        return [str(channel).strip()]
    return []


def map_canal_label(label: str) -> str | None:
    """One Canal value → one Metricool network. LinkedIn/Newsletter/IG Nico → None."""
    if not label or _is_ig_nico(label) or _is_newsletter(label) or _is_linkedin_only(label):
        return None
    explicit = explicit_canal_network(label)
    if explicit:
        return explicit
    if label_is_youtube_short(label):
        return "youtube"
    compact = _compact_label(label)
    if compact in {"tiktok", "tik tok".replace(" ", "")}:
        return "tiktok"
    if _fold_label(label) in {"tik tok"}:
        return "tiktok"
    if compact == "youtube":
        return "youtube"
    network = normalize_channel(label)
    if network in (None, "linkedin"):
        return None
    return network


@dataclass(frozen=True)
class CanalPlan:
    """Networks to put on one Metricool post, in Canal order."""

    networks: tuple[str, ...]
    youtube_short: bool
    skip_nico: bool
    unrecognized: tuple[str, ...]
    dropped: tuple[str, ...] = ()


def plan_canals(
    labels: list[str],
    *,
    exclude: frozenset[str] = frozenset(),
    connected: frozenset[str] | None = None,
) -> CanalPlan:
    """Map Canal values onto networks this brand can publish.

    Labels that are not a connected network (Luma, WhatsApp, Newsletter,
    LinkedIn, an unknown name, Facebook when the brand has no page) are
    dropped. They do not block the row. IG Nico still skips the whole row.
    ``connected`` defaults to Instagram, TikTok, and YouTube.
    """
    allowed = connected if connected is not None else FALLBACK_CONNECTED_NETWORKS
    allowed = frozenset(net for net in allowed if net not in NEVER_SCHEDULE_NETWORKS)
    if any(_is_ig_nico(label) for label in labels):
        return CanalPlan((), False, True, (), ())
    networks: list[str] = []
    youtube_short = False
    dropped: list[str] = []
    for label in labels:
        if (
            _is_newsletter(label)
            or _is_linkedin_only(label)
            or _excluded_label(label, exclude)
        ):
            dropped.append(label)
            continue
        network = map_canal_label(label)
        if network is None or network not in allowed:
            dropped.append(label)
            continue
        if network not in networks:
            networks.append(network)
        if network == "youtube" and label_is_youtube_short(label):
            youtube_short = True
    return CanalPlan(tuple(networks), youtube_short, False, tuple(dropped), tuple(dropped))
