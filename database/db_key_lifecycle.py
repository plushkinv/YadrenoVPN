"""Идемпотентный лог событий жизненного цикла ключей."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'get_pending_expired_key_events',
    'record_key_lifecycle_event_once',
]


def get_pending_expired_key_events(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает истёкшие ключи, для которых key_expired ещё не записан."""
    sql = """
        SELECT
            vk.*,
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
           AND ev.event_token = COALESCE(vk.expires_at, '')
        WHERE vk.expires_at IS NOT NULL
          AND vk.expires_at <= datetime('now')
          AND ev.id IS NULL
        ORDER BY vk.expires_at ASC, vk.id ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(0, int(limit)),)

    with get_db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def record_key_lifecycle_event_once(
    *,
    key_id: int,
    event_name: str,
    event_token: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Записывает lifecycle-событие один раз и возвращает True только при первой записи."""
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO key_lifecycle_event_log (
                vpn_key_id, event_name, event_token, metadata_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (key_id, event_name, event_token, metadata_json),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            logger.info(
                "Lifecycle-событие %s для ключа %s записано с token=%s",
                event_name,
                key_id,
                event_token,
            )
        return inserted
