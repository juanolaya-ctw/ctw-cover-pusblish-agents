"""Validate .env credentials against Notion and Metricool (no side effects)."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import timedelta

import httpx
from notion_client import Client
from notion_client.errors import APIResponseError

from metricool_sync_posts.config import load_settings
from metricool_sync_posts.timeutil import iso_metricool, now_in

_PLACEHOLDER_NOTION = re.compile(
    r"^(secret_xxx|xxx+|changeme|your_.*|replace_.*)$",
    re.I,
)
_PLACEHOLDER_METRICOOL = re.compile(
    r"^(your_x_mc_auth_token|xxx+|changeme|replace_.*)$",
    re.I,
)
_PLACEHOLDER_USER_ID = re.compile(r"^(0+|0000000)$")


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def _check_placeholders(settings) -> bool:
    ok = True
    if _PLACEHOLDER_NOTION.match(settings.notion_token.strip()):
        _fail(
            "NOTION_TOKEN looks like a placeholder. Paste the real integration secret in .env "
            "(Notion → Integrations → CTW integration → Internal Integration Secret)."
        )
        ok = False
    db = settings.notion_database_id.strip()
    if not db or "xxxx" in db.lower():
        _fail(
            "NOTION_DATABASE_ID is missing or placeholder. Run: "
            "python -m metricool_sync_posts.cli.discover --query Parrilla"
        )
        ok = False
    if _PLACEHOLDER_METRICOOL.match(settings.metricool_user_token.strip()):
        _fail(
            "METRICOOL_USER_TOKEN looks like a placeholder. Copy X-Mc-Auth from Metricool "
            "(Ajustes → API, plan Advanced/Custom)."
        )
        ok = False
    if _PLACEHOLDER_USER_ID.match(settings.metricool_user_id.strip()):
        _fail("METRICOOL_USER_ID looks like a placeholder (use your numeric Metricool userId).")
        ok = False
    return ok


def _ping_notion(token: str) -> bool:
    client = Client(auth=token)
    try:
        me = client.users.me()
        name = me.get("name") or me.get("id") or "integration"
        _ok(f"Notion auth — users/me ({name})")
        return True
    except APIResponseError as exc:
        if exc.status in (401, 403):
            _fail(
                f"Notion returned {exc.status}. Token invalid or integration not in workspace. "
                "On Windows: paste NOTION_TOKEN in .env (cloud agents cannot read your PC)."
            )
        else:
            _fail(f"Notion API error: {exc.status} {exc.code}")
        return False


def _ping_notion_database(token: str, database_id: str) -> bool:
    client = Client(auth=token)
    try:
        ds = client.data_sources.retrieve(database_id)
        title_parts = ds.get("title") or []
        title = "".join(p.get("plain_text", "") for p in title_parts if isinstance(p, dict))
        _ok(f"Notion data source {database_id} — {title or '(sin título)'}")
        return True
    except APIResponseError as exc:
        if exc.status in (401, 403):
            _fail(
                f"Notion data source {exc.status}. Check NOTION_TOKEN and connect the integration "
                "to Parrilla CTW (⋯ → Connections)."
            )
        elif exc.status == 404:
            _fail(
                f"NOTION_DATABASE_ID {database_id!r} not found for this token. "
                "Re-run discover --query Parrilla."
            )
        else:
            _fail(f"Notion data source error: {exc.status} {exc.code}")
        return False


def _ping_metricool(settings) -> bool:
    now = now_in(settings.timezone)
    from_dt = now - timedelta(days=1)
    to_dt = now + timedelta(days=1)
    params = {
        "userId": settings.metricool_user_id,
        "blogId": settings.metricool_blog_id,
        "from": iso_metricool(from_dt),
        "to": iso_metricool(to_dt),
        "timezone": settings.metricool_timezone,
        "extendedRange": "true",
    }
    try:
        with httpx.Client(
            base_url=settings.metricool_base_url.rstrip("/"),
            headers={
                "X-Mc-Auth": settings.metricool_user_token,
                "Content-Type": "application/json",
            },
            timeout=60.0,
        ) as http:
            resp = http.get("/v2/scheduler/posts", params=params)
            if resp.status_code == 401:
                _fail(
                    "Metricool 401 — check METRICOOL_USER_TOKEN, METRICOOL_USER_ID, and "
                    f"METRICOOL_BLOG_ID={settings.metricool_blog_id}."
                )
                return False
            resp.raise_for_status()
            data = resp.json()
            count = len(data) if isinstance(data, list) else 0
            if isinstance(data, dict):
                for key in ("data", "posts", "items", "results"):
                    if isinstance(data.get(key), list):
                        count = len(data[key])
                        break
            _ok(
                f"Metricool scheduler API — blogId {settings.metricool_blog_id}, "
                f"sample window returned {count} post(s)"
            )
            return True
    except httpx.HTTPStatusError as exc:
        _fail(f"Metricool HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        return False
    except httpx.RequestError as exc:
        _fail(f"Metricool request failed: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate Notion + Metricool credentials in .env (exit 0 if all checks pass)"
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Only check for placeholder values, do not call APIs",
    )
    args = parser.parse_args()
    settings = load_settings()

    all_ok = _check_placeholders(settings)
    if not all_ok:
        sys.exit(1)
    if args.skip_network:
        _ok("Placeholder checks only (--skip-network)")
        sys.exit(0)

    all_ok = _ping_notion(settings.notion_token) and all_ok
    all_ok = _ping_notion_database(settings.notion_token, settings.notion_database_id) and all_ok
    all_ok = _ping_metricool(settings) and all_ok
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
