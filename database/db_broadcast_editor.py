"""Atomic settings storage for the contextual broadcast editor."""

from __future__ import annotations

import json
from typing import Any, Optional

from .connection import get_db

__all__ = [
    "apply_broadcast_editor_stage",
    "compare_and_swap_broadcast_stage",
    "delete_broadcast_confirmation",
    "delete_broadcast_editor_stage",
    "get_broadcast_confirmation_raw",
    "get_broadcast_editor_snapshot",
    "insert_broadcast_stage_if_absent",
    "pop_broadcast_confirmation_raw",
    "set_broadcast_confirmation_raw",
    "set_broadcast_content_with_revision",
    "set_broadcast_filter_with_revision",
]

BROADCAST_CONTENT_SETTING = "broadcast_message"
BROADCAST_FILTER_SETTING = "broadcast_filter"
BROADCAST_STYLE_SETTING = "broadcast_style_profile"
BROADCAST_CONFIG_REVISION_SETTING = "broadcast_config_revision"
BROADCAST_STAGE_PREFIX = "broadcast_editor_stage"
BROADCAST_CONFIRM_PREFIX = "broadcast_confirm"


def _stage_key(telegram_id: int) -> str:
    return f"{BROADCAST_STAGE_PREFIX}:{int(telegram_id)}"


def _confirm_key(telegram_id: int) -> str:
    return f"{BROADCAST_CONFIRM_PREFIX}:{int(telegram_id)}"


def _read_setting(conn: Any, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _write_setting(conn: Any, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _safe_revision(raw: Optional[str]) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _stage_revision(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        value = payload.get("stage_revision") if isinstance(payload, dict) else None
        return int(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _invalidate_confirmations(conn: Any) -> None:
    conn.execute(
        "DELETE FROM settings WHERE key LIKE ?",
        (f"{BROADCAST_CONFIRM_PREFIX}:%",),
    )


def get_broadcast_editor_snapshot(telegram_id: int) -> dict[str, Optional[str]]:
    """Read all settings required to reconstruct one administrator's stage."""
    keys = (
        BROADCAST_CONTENT_SETTING,
        BROADCAST_FILTER_SETTING,
        BROADCAST_STYLE_SETTING,
        BROADCAST_CONFIG_REVISION_SETTING,
        _stage_key(telegram_id),
    )
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({','.join('?' for _ in keys)})",
            keys,
        ).fetchall()
    values = {str(row["key"]): row["value"] for row in rows}
    return {
        "content": values.get(BROADCAST_CONTENT_SETTING),
        "filter": values.get(BROADCAST_FILTER_SETTING),
        "style": values.get(BROADCAST_STYLE_SETTING),
        "config_revision": values.get(BROADCAST_CONFIG_REVISION_SETTING),
        "stage": values.get(_stage_key(telegram_id)),
    }


def insert_broadcast_stage_if_absent(telegram_id: int, raw_stage: str) -> bool:
    """Insert a newly built stage without replacing a concurrent creator."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (_stage_key(telegram_id), raw_stage),
        )
        return cursor.rowcount > 0


def compare_and_swap_broadcast_stage(
    telegram_id: int,
    expected_stage_revision: int,
    raw_stage: str,
) -> tuple[bool, Optional[str]]:
    """Replace a stage only when its embedded revision matches the caller."""
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_raw = _read_setting(conn, _stage_key(telegram_id))
        if _stage_revision(current_raw) != int(expected_stage_revision):
            return False, current_raw
        _write_setting(conn, _stage_key(telegram_id), raw_stage)
        return True, raw_stage


def delete_broadcast_editor_stage(telegram_id: int) -> bool:
    """Delete the durable editor stage for one administrator."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM settings WHERE key = ?",
            (_stage_key(telegram_id),),
        )
        return cursor.rowcount > 0


def _set_working_value_with_revision(key: str, value: str) -> int:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        revision = _safe_revision(_read_setting(conn, BROADCAST_CONFIG_REVISION_SETTING)) + 1
        _write_setting(conn, key, value)
        _write_setting(conn, BROADCAST_CONFIG_REVISION_SETTING, str(revision))
        _invalidate_confirmations(conn)
        return revision


def set_broadcast_content_with_revision(raw_content: str) -> int:
    """Persist manually edited content and advance the shared config revision."""
    return _set_working_value_with_revision(BROADCAST_CONTENT_SETTING, raw_content)


def set_broadcast_filter_with_revision(filter_key: str) -> int:
    """Persist a manually selected filter and advance the shared config revision."""
    return _set_working_value_with_revision(BROADCAST_FILTER_SETTING, filter_key)


def apply_broadcast_editor_stage(
    telegram_id: int,
    *,
    expected_stage_revision: int,
    expected_config_revision: int,
    raw_content: str,
    filter_key: str,
    raw_style: Optional[str],
    raw_saved_stage: str,
) -> dict[str, Any]:
    """Atomically apply a stage to working settings with two revision checks."""
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_stage = _read_setting(conn, _stage_key(telegram_id))
        current_config = _safe_revision(
            _read_setting(conn, BROADCAST_CONFIG_REVISION_SETTING)
        )
        if _stage_revision(current_stage) != int(expected_stage_revision):
            return {
                "status": "stage_conflict",
                "stage": current_stage,
                "config_revision": current_config,
            }
        if current_config != int(expected_config_revision):
            return {
                "status": "config_conflict",
                "stage": current_stage,
                "config_revision": current_config,
            }

        next_revision = current_config + 1
        _write_setting(conn, BROADCAST_CONTENT_SETTING, raw_content)
        _write_setting(conn, BROADCAST_FILTER_SETTING, filter_key)
        if raw_style is not None:
            _write_setting(conn, BROADCAST_STYLE_SETTING, raw_style)
        _write_setting(conn, BROADCAST_CONFIG_REVISION_SETTING, str(next_revision))
        _write_setting(conn, _stage_key(telegram_id), raw_saved_stage)
        _invalidate_confirmations(conn)
        return {
            "status": "ok",
            "stage": raw_saved_stage,
            "config_revision": next_revision,
        }


def set_broadcast_confirmation_raw(telegram_id: int, raw_confirmation: str) -> None:
    """Replace the one-time launch confirmation for an administrator."""
    with get_db() as conn:
        _write_setting(conn, _confirm_key(telegram_id), raw_confirmation)


def get_broadcast_confirmation_raw(telegram_id: int) -> Optional[str]:
    """Read the current one-time launch confirmation."""
    with get_db() as conn:
        return _read_setting(conn, _confirm_key(telegram_id))


def pop_broadcast_confirmation_raw(telegram_id: int, token: str) -> Optional[str]:
    """Consume a confirmation only when its token matches."""
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        raw = _read_setting(conn, _confirm_key(telegram_id))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict) or payload.get("token") != token:
            return None
        conn.execute("DELETE FROM settings WHERE key = ?", (_confirm_key(telegram_id),))
        return raw


def delete_broadcast_confirmation(telegram_id: int) -> bool:
    """Delete a pending launch confirmation."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM settings WHERE key = ?",
            (_confirm_key(telegram_id),),
        )
        return cursor.rowcount > 0
