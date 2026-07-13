"""Database operations for resetting local customization only."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Iterable

from database import connection as db_connection
from database import migrations

STOCK_CUSTOM_PAGE_KEYS = {"custom_profile"}
STOCK_PAGE_ROUTES = {
    "profile": {
        "page_key": "custom_profile",
        "guard_names": '["not_banned"]',
        "hook_names": "[]",
        "is_enabled": 1,
    },
}
EXTENSION_TABLE_RE = re.compile(r"^ext_[a-z][a-z0-9_]{0,47}_[a-z][a-z0-9_]{0,63}$")


def _db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else Path(db_connection.DB_PATH)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table_name}")')}


def _count_where(
    conn: sqlite3.Connection,
    table_name: str,
    where_sql: str,
    params: Iterable[object] = (),
) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(
        f'SELECT COUNT(*) AS count FROM "{table_name}" WHERE {where_sql}',
        tuple(params),
    ).fetchone()
    return int(row["count"] if row else 0)


def _customization_default_settings() -> dict[str, str | None]:
    return {
        "my_keys_item_template": migrations._my_keys_item_template(),
        "notification_text": (
            "⚠️ <b>Ваш VPN-ключ %ключ_имя% скоро истекает!</b>\n\n"
            "Через %ключ_дней_до_окончания% дней закончится срок действия вашего ключа.\n\n"
            "Продлите подписку, чтобы сохранить доступ к VPN без перерывов!"
        ),
        "traffic_notification_text": (
            "⚠️ По ключу <b>%ключ_имя%</b> осталось "
            "%ключ_трафик_процент_остатка%% трафика "
            "(%ключ_трафик_использовано% из %ключ_трафик_лимит%)"
        ),
        "referral_new_ref_notification_text": migrations._referral_new_ref_notification_text(),
        "referral_purchase_notification_text": migrations._referral_purchase_notification_text(),
        "broadcast_message": None,
        "broadcast_in_progress": "0",
        "broadcast_stop_requested": "0",
        "custom_extensions_enabled": "0",
        "custom_payment_webhooks_enabled": "0",
        "custom_payment_webhooks_host": "127.0.0.1",
        "custom_payment_webhooks_port": "8088",
        "custom_payment_webhooks_path_prefix": "/custom-payment-webhook",
        "yadreno_admin_customization_enabled": "0",
        "yadreno_admin_core_changes_enabled": "0",
    }


def _reset_pages(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    if not _table_exists(conn, "pages"):
        return ["pages table is missing; skipped"]

    page_columns = _columns(conn, "pages")
    custom_columns = [
        column
        for column in ("text_custom", "image_custom", "media_type_custom", "buttons_custom")
        if column in page_columns
    ]
    flow_columns = [
        column for column in ("guard_names", "hook_names") if column in page_columns
    ]
    actions: list[str] = []

    if custom_columns:
        where = " OR ".join(f"{column} IS NOT NULL" for column in custom_columns)
        affected = _count_where(conn, "pages", where)
        actions.append(f"pages custom fields to NULL: {affected} row(s)")
        if affected and not dry_run:
            set_sql = ", ".join(f"{column} = NULL" for column in custom_columns)
            conn.execute(f"UPDATE pages SET {set_sql} WHERE {where}")

    if flow_columns:
        where = " OR ".join(f"COALESCE({column}, '[]') != '[]'" for column in flow_columns)
        affected = _count_where(conn, "pages", where)
        actions.append(f"pages guard/hook flow to []: {affected} row(s)")
        if affected and not dry_run:
            set_sql = ", ".join(f"{column} = '[]'" for column in flow_columns)
            conn.execute(f"UPDATE pages SET {set_sql} WHERE {where}")

    return actions


def _delete_non_stock_custom_pages(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    if not _table_exists(conn, "pages"):
        return []

    placeholders = ", ".join("?" for _ in STOCK_CUSTOM_PAGE_KEYS)
    affected = _count_where(
        conn,
        "pages",
        f"page_key LIKE 'custom\\_%' ESCAPE '\\' AND page_key NOT IN ({placeholders})",
        STOCK_CUSTOM_PAGE_KEYS,
    )
    actions = [f"non-stock custom_* pages to delete: {affected} row(s)"]
    if affected and not dry_run:
        conn.execute(
            f"""
            DELETE FROM pages
            WHERE page_key LIKE 'custom\\_%' ESCAPE '\\'
              AND page_key NOT IN ({placeholders})
            """,
            tuple(STOCK_CUSTOM_PAGE_KEYS),
        )
    return actions


def _reset_page_routes(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    if not _table_exists(conn, "page_routes"):
        return ["page_routes table is missing; skipped"]

    stock_route_keys = set(STOCK_PAGE_ROUTES)
    placeholders = ", ".join("?" for _ in stock_route_keys)
    actions: list[str] = []
    custom_routes = _count_where(
        conn,
        "page_routes",
        f"route_key NOT IN ({placeholders})",
        stock_route_keys,
    )
    actions.append(f"non-stock page routes to delete: {custom_routes} row(s)")
    if custom_routes and not dry_run:
        conn.execute(
            f"DELETE FROM page_routes WHERE route_key NOT IN ({placeholders})",
            tuple(stock_route_keys),
        )

    for route_key, route in STOCK_PAGE_ROUTES.items():
        page_exists = _count_where(conn, "pages", "page_key = ?", (route["page_key"],)) > 0
        if not page_exists:
            actions.append(f"stock page route {route_key!r} skipped: page is missing")
            continue
        current = conn.execute(
            """
            SELECT page_key, guard_names, hook_names, is_enabled
            FROM page_routes
            WHERE route_key = ?
            """,
            (route_key,),
        ).fetchone()
        needs_upsert = (
            current is None
            or current["page_key"] != route["page_key"]
            or current["guard_names"] != route["guard_names"]
            or current["hook_names"] != route["hook_names"]
            or int(current["is_enabled"]) != int(route["is_enabled"])
        )
        actions.append(
            f"stock page route {route_key!r}: {'reset' if needs_upsert else 'already stock'}"
        )
        if needs_upsert and not dry_run:
            conn.execute(
                """
                INSERT INTO page_routes (
                    route_key, page_key, guard_names, hook_names, is_enabled, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(route_key) DO UPDATE SET
                    page_key = excluded.page_key,
                    guard_names = excluded.guard_names,
                    hook_names = excluded.hook_names,
                    is_enabled = excluded.is_enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    route_key,
                    route["page_key"],
                    route["guard_names"],
                    route["hook_names"],
                    route["is_enabled"],
                ),
            )

    return actions


def _reset_settings(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    if not _table_exists(conn, "settings"):
        return ["settings table is missing; skipped"]

    actions: list[str] = []
    for key, value in _customization_default_settings().items():
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if value is None:
            exists = row is not None
            actions.append(f"settings.{key}: {'delete' if exists else 'already absent'}")
            if exists and not dry_run:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            continue

        changed = row is None or row["value"] != value
        actions.append(f"settings.{key}: {'reset' if changed else 'already stock'}")
        if changed and not dry_run:
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
    return actions


def _reset_extension_tables(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    actions: list[str] = []

    for table_name in ("extension_storage", "extension_schema_versions", "extension_core_operations"):
        if not _table_exists(conn, table_name):
            actions.append(f"{table_name} table is missing; skipped")
            continue
        count = _count_where(conn, table_name, "1 = 1")
        actions.append(f"{table_name} rows to delete: {count}")
        if count and not dry_run:
            conn.execute(f'DELETE FROM "{table_name}"')

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    extension_tables = [
        str(row["name"]) for row in rows if EXTENSION_TABLE_RE.fullmatch(str(row["name"]))
    ]
    actions.append(
        "extension data tables to drop: "
        + (", ".join(extension_tables) if extension_tables else "none")
    )
    if not dry_run:
        for table_name in extension_tables:
            conn.execute(f'DROP TABLE "{table_name}"')
    return actions


def reset_customization_database(
    db_path: str | Path | None = None,
    *,
    dry_run: bool = True,
) -> list[str]:
    """Resets only customization tables/fields and returns an action report."""
    path = _db_path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")

    conn = _connect(path)
    try:
        actions: list[str] = []
        actions.extend(_reset_pages(conn, dry_run))
        actions.extend(_reset_page_routes(conn, dry_run))
        actions.extend(_delete_non_stock_custom_pages(conn, dry_run))
        actions.extend(_reset_settings(conn, dry_run))
        actions.extend(_reset_extension_tables(conn, dry_run))

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return actions
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "EXTENSION_TABLE_RE",
    "STOCK_CUSTOM_PAGE_KEYS",
    "STOCK_PAGE_ROUTES",
    "reset_customization_database",
]
