"""Idempotent key life cycle event log."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .connection import get_db
from .key_inactivity import inactive_key_sql, inactive_since_sql, inactivity_token_sql

logger = logging.getLogger(__name__)

__all__ = [
    'get_pending_expired_key_events',
    'record_expired_key_event_once',
    'record_key_lifecycle_event_once',
]


_PENDING_EXPIRED_KEYS = f"""
        SELECT
            vk.*,
            {inactive_since_sql('vk')} AS effective_inactive_since,
            {inactivity_token_sql('vk')} AS event_token,
            u.telegram_id,
            u.username,
            u.is_banned,
            t.name AS tariff_name,
            s.name AS server_name
        FROM vpn_keys vk
        JOIN users u ON u.id = vk.user_id
        LEFT JOIN tariffs t ON t.id = vk.tariff_id
        LEFT JOIN servers s ON s.id = vk.server_id
        LEFT JOIN key_lifecycle_event_log ev
            ON ev.vpn_key_id = vk.id
           AND ev.event_name = 'key_expired'
           AND ev.event_token = {inactivity_token_sql('vk')}
        WHERE {inactive_key_sql('vk')}
          AND {inactivity_token_sql('vk')} IS NOT NULL
          AND ev.id IS NULL
    """


def get_pending_expired_key_events(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return inactive keys whose current episode has not emitted key_expired."""
    sql = _PENDING_EXPIRED_KEYS + " ORDER BY datetime(effective_inactive_since), vk.id"
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(0, int(limit)),)

    with get_db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def record_expired_key_event_once(
    *, key_id: int, event_token: str, subscribers=(),
) -> Optional[Dict[str, Any]]:
    """Atomically claim a current inactivity episode and its event/legacy data."""
    from .db_core_events import record_core_event_with_conn

    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            _PENDING_EXPIRED_KEYS + f' AND vk.id = ? AND {inactivity_token_sql("vk")} = ?',
            (key_id, event_token),
        ).fetchone()
        if row is None:
            return None
        key = dict(row)
        conn.execute('''UPDATE vpn_keys SET inactive_since = ?, inactive_event_token = ?
            WHERE id = ? AND inactive_since IS NULL''',
            (key['effective_inactive_since'], event_token, key_id),
        )
        lifecycle_id = _record_key_lifecycle_event_with_conn(
            conn, key_id=key_id, event_name='key_expired', event_token=event_token,
            metadata={
                name: key.get(name)
                for name in ('expires_at', 'telegram_id', 'tariff_id', 'server_id')
            },
        )
        if lifecycle_id is None:
            return None
        record_core_event_with_conn(
            conn, event_name='key.expired', source_id=str(lifecycle_id),
            expired_key=key, subscribers=subscribers,
        )
        return key


def record_key_lifecycle_event_once(
    *,
    key_id: int,
    event_name: str,
    event_token: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Writes a lifecycle event once and returns True only the first time it is written."""
    with get_db() as conn:
        return _record_key_lifecycle_event_with_conn(
            conn, key_id=key_id, event_name=event_name,
            event_token=event_token, metadata=metadata,
        ) is not None


def _record_key_lifecycle_event_with_conn(
    conn, *, key_id: int, event_name: str, event_token: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Return the newly inserted marker id without committing the caller's transaction."""
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO key_lifecycle_event_log (
            vpn_key_id, event_name, event_token, metadata_json
        )
        VALUES (?, ?, ?, ?)
        """,
        (key_id, event_name, event_token, metadata_json),
    )
    if cursor.rowcount == 0:
        return None
    logger.info(
        "Lifecycle-событие %s для ключа %s записано с token=%s",
        event_name, key_id, event_token,
    )
    return int(cursor.lastrowid)
