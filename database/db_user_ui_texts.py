"""Database access for non-page core user interface fragments."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from .connection import get_db


__all__ = [
    "clear_all_user_ui_text_custom",
    "clear_user_ui_text_custom",
    "get_all_user_ui_texts",
    "get_user_ui_text",
    "get_user_ui_texts_fingerprint",
    "set_user_ui_text_custom",
    "update_user_ui_text_defaults",
]


def _effective_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    custom = data.get("text_custom")
    data["text_effective"] = custom if custom is not None else data["text_default"]
    return data


def get_all_user_ui_texts() -> list[dict[str, Any]]:
    """Returns the complete catalog with resolved effective values."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT text_key, text_default, text_custom, text_format, description, updated_at
            FROM user_ui_texts
            ORDER BY text_key
            """
        ).fetchall()
    return [_effective_row(row) for row in rows]


def get_user_ui_text(text_key: str) -> dict[str, Any] | None:
    """Returns one catalog row with its effective value."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT text_key, text_default, text_custom, text_format, description, updated_at
            FROM user_ui_texts
            WHERE text_key = ?
            """,
            (text_key,),
        ).fetchone()
    return _effective_row(row) if row else None


def set_user_ui_text_custom(text_key: str, text_custom: str) -> bool:
    """Sets a custom value and returns whether an existing row was updated."""
    if not isinstance(text_custom, str):
        raise TypeError("text_custom must be a string")
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE user_ui_texts
            SET text_custom = ?, updated_at = CURRENT_TIMESTAMP
            WHERE text_key = ?
            """,
            (text_custom, text_key),
        )
        return cursor.rowcount > 0


def clear_user_ui_text_custom(text_key: str) -> bool:
    """Clears one custom value and returns whether an existing row was found."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE user_ui_texts
            SET text_custom = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE text_key = ?
            """,
            (text_key,),
        )
        return cursor.rowcount > 0


def clear_all_user_ui_text_custom() -> int:
    """Clears every active UI override and returns the affected row count."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE user_ui_texts
            SET text_custom = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE text_custom IS NOT NULL
            """
        )
        return int(cursor.rowcount)


def get_user_ui_texts_fingerprint() -> str:
    """Returns a stable value-based fingerprint for cache invalidation."""
    payload = [
        {
            "text_key": row["text_key"],
            "text_effective": row["text_effective"],
            "text_format": row["text_format"],
        }
        for row in get_all_user_ui_texts()
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_user_ui_text_defaults(
    definitions: Iterable[Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Upserts developer defaults without replacing administrator overrides."""
    items = tuple(definitions)

    def apply(target: sqlite3.Connection) -> None:
        target.executemany(
            """
            INSERT INTO user_ui_texts (
                text_key, text_default, text_format, description
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(text_key) DO UPDATE SET
                text_default = excluded.text_default,
                text_format = excluded.text_format,
                description = excluded.description,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                (
                    item.text_key,
                    item.text_default,
                    item.text_format,
                    item.description,
                )
                for item in items
            ],
        )

    if conn is not None:
        apply(conn)
    else:
        with get_db() as target:
            apply(target)
    return len(items)
