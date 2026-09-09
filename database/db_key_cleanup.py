"""Atomic retention cleanup for time/traffic-inactive VPN keys."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Dict, List, Optional

from .connection import get_db
from .key_inactivity import inactive_key_sql, inactive_since_sql

logger = logging.getLogger(__name__)

__all__ = [
    "get_expired_keys_older_than",
    "delete_expired_keys_older_than",
]


def _validate_age_days(value: int, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        qualifier = "a non-negative" if allow_zero else "a positive"
        raise ValueError(f"age_days must be {qualifier} integer")
    return value


def _select_expired_keys(
    conn,
    age_days: int,
    *,
    eligible_key_ids: Optional[Iterable[int]] = None,
) -> List[Dict[str, Any]]:
    cutoff_modifier = f"-{age_days} days"
    params: list[Any] = [cutoff_modifier]
    eligible_clause = ""
    if eligible_key_ids is not None:
        normalized_ids = sorted({int(key_id) for key_id in eligible_key_ids})
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        eligible_clause = f" AND vk.id IN ({placeholders})"
        params.extend(normalized_ids)

    rows = conn.execute(
        f"""
        SELECT
            vk.id,
            vk.user_id,
            vk.server_id,
            vk.custom_name,
            vk.panel_email,
            vk.expires_at,
            {inactive_since_sql('vk')} AS inactive_since,
            u.telegram_id
        FROM vpn_keys vk
        JOIN users u ON u.id = vk.user_id
        WHERE {inactive_key_sql('vk')}
          AND datetime({inactive_since_sql('vk')}) <= datetime('now', ?)
          {eligible_clause}
        ORDER BY u.telegram_id ASC, datetime({inactive_since_sql('vk')}) ASC, vk.id ASC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_expired_keys_older_than(age_days: int) -> List[Dict[str, Any]]:
    """Return continuously time/traffic-inactive keys without mutating them."""
    normalized_days = _validate_age_days(age_days, allow_zero=True)
    with get_db() as conn:
        return _select_expired_keys(conn, normalized_days)


def delete_expired_keys_older_than(
    retention_days: int,
    *,
    eligible_key_ids: Optional[Iterable[int]] = None,
) -> List[Dict[str, Any]]:
    """Atomically delete the eligible keys still beyond retention.

    Omitting ``eligible_key_ids`` preserves the historical database helper
    contract. The scheduled cleanup supplies only keys whose panel state has
    already been confirmed safe.
    """
    normalized_days = _validate_age_days(retention_days, allow_zero=False)

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = _select_expired_keys(
            conn,
            normalized_days,
            eligible_key_ids=eligible_key_ids,
        )
        if not deleted:
            return []

        key_ids = [int(row["id"]) for row in deleted]
        placeholders = ",".join("?" for _ in key_ids)
        conn.execute(
            f"UPDATE payments SET vpn_key_id = NULL "
            f"WHERE vpn_key_id IN ({placeholders})",
            key_ids,
        )
        conn.execute(
            f"DELETE FROM notification_log "
            f"WHERE vpn_key_id IN ({placeholders})",
            key_ids,
        )
        cursor = conn.execute(
            f"DELETE FROM vpn_keys WHERE id IN ({placeholders})",
            key_ids,
        )
        if cursor.rowcount != len(key_ids):
            raise RuntimeError(
                "Expired-key cleanup deleted an unexpected number of rows"
            )

    logger.info(
        "Deleted %s VPN keys expired for at least %s days",
        len(deleted),
        normalized_days,
    )
    return deleted
