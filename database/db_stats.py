import sqlite3
import logging
import secrets
import string
import datetime
import json
from collections.abc import Iterable
from typing import Optional, List, Dict, Any, Tuple
from .connection import get_db

logger = logging.getLogger(__name__)

BROADCAST_FILTER_KEYS = (
    'active',
    'inactive',
    'never_paid',
    'expired',
    'used_trial',
)
_BROADCAST_FILTER_KEY_SET = frozenset(BROADCAST_FILTER_KEYS)

__all__ = [
    'BROADCAST_FILTER_KEYS',
    'BroadcastFilterError',
    'encode_broadcast_filters',
    'normalize_broadcast_filters',
    'get_users_for_broadcast',
    'count_users_for_broadcast',
    'get_expiring_keys',
    'is_notification_sent_today',
    'log_notification_sent',
    'get_keys_stats',
]


class BroadcastFilterError(ValueError):
    """Raised when a stored or supplied broadcast filter selection is invalid."""


def normalize_broadcast_filters(
    value: object,
    *,
    allow_legacy: bool = True,
) -> tuple[str, ...]:
    """
    Return a unique broadcast filter tuple in canonical UI order.

    An empty iterable is the only canonical representation of "all eligible
    users". Legacy scalar values remain readable during upgrades.
    """
    if value is None:
        raise BroadcastFilterError('Broadcast filter selection is missing')
    if isinstance(value, str):
        raw = value.strip()
        if allow_legacy and raw == 'all':
            raw_items = []
        elif allow_legacy and raw in _BROADCAST_FILTER_KEY_SET:
            raw_items = [raw]
        else:
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as error:
                raise BroadcastFilterError(
                    'Invalid broadcast filter selection'
                ) from error
            if not isinstance(parsed, list):
                raise BroadcastFilterError(
                    'Broadcast filter selection must be a JSON array'
                )
            raw_items = parsed
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        raw_items = list(value)
    else:
        raise BroadcastFilterError('Invalid broadcast filter selection')

    selected: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str) or item not in _BROADCAST_FILTER_KEY_SET:
            raise BroadcastFilterError(f'Unknown broadcast filter: {item!r}')
        selected.add(item)
    return tuple(key for key in BROADCAST_FILTER_KEYS if key in selected)


def encode_broadcast_filters(value: object) -> str:
    """Serialize a valid filter selection as a compact canonical JSON array."""
    return json.dumps(
        list(normalize_broadcast_filters(value)),
        ensure_ascii=False,
        separators=(',', ':'),
    )


def _broadcast_recipient_query_parts(
    filters: object,
) -> tuple[list[str], tuple[object, ...]]:
    selected = normalize_broadcast_filters(filters)
    conditions = [
        'u.is_banned = 0',
        'u.is_bot_blocked = 0',
    ]

    if 'active' in selected:
        conditions.append(
            """
            EXISTS (
                SELECT 1 FROM vpn_keys active_key
                WHERE active_key.user_id = u.id
                  AND (
                        active_key.expires_at > datetime('now')
                        OR active_key.expires_at IS NULL
                  )
            )
            """
        )
    if 'inactive' in selected:
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1 FROM vpn_keys active_key
                WHERE active_key.user_id = u.id
                  AND (
                        active_key.expires_at > datetime('now')
                        OR active_key.expires_at IS NULL
                  )
            )
            """
        )
    if 'never_paid' in selected:
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1 FROM payments paid_order
                WHERE paid_order.user_id = u.id
                  AND paid_order.status = 'paid'
                  AND COALESCE(
                        NULLIF(paid_order.purpose, ''),
                        'legacy_key_payment'
                      ) IN (
                        'legacy_key_payment',
                        'key_purchase',
                        'key_renewal'
                      )
                  AND COALESCE(paid_order.payment_type, '') NOT IN (
                        'trial',
                        'promo_free',
                        'demo'
                      )
                  AND COALESCE(paid_order.is_promo_free, 0) = 0
            )
            """
        )
    if 'expired' in selected:
        conditions.extend(
            (
                """
                EXISTS (
                    SELECT 1 FROM vpn_keys expired_key
                    WHERE expired_key.user_id = u.id
                      AND expired_key.expires_at <= datetime('now')
                )
                """,
                """
                NOT EXISTS (
                    SELECT 1 FROM vpn_keys active_key
                    WHERE active_key.user_id = u.id
                      AND (
                            active_key.expires_at > datetime('now')
                            OR active_key.expires_at IS NULL
                      )
                )
                """,
            )
        )
    if 'used_trial' in selected:
        conditions.append('COALESCE(u.used_trial, 0) = 1')

    return conditions, ()


def get_users_for_broadcast(filters: object = ()) -> List[int]:
    """
    Gets telegram ids matching all selected broadcast filters.

    Args:
        filters: Filter keys. An empty selection means all eligible users.
            Legacy values ``all`` and one scalar key remain supported.

    Returns:
        List of telegram_id users
    """
    try:
        conditions, params = _broadcast_recipient_query_parts(filters)
    except BroadcastFilterError as error:
        logger.error('Broadcast recipient selection rejected: %s', error)
        return []

    with get_db() as conn:
        cursor = conn.execute(
            'SELECT u.telegram_id FROM users u WHERE ' + ' AND '.join(conditions),
            params,
        )
        return [row['telegram_id'] for row in cursor.fetchall()]


def count_users_for_broadcast(filters: object = ()) -> int:
    """
    Counts users matching all selected broadcast filters.

    Args:
        filters: Filter keys accepted by :func:`get_users_for_broadcast`.

    Returns:
        Number of users
    """
    try:
        conditions, params = _broadcast_recipient_query_parts(filters)
    except BroadcastFilterError as error:
        logger.error('Broadcast recipient selection rejected: %s', error)
        return 0

    with get_db() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS cnt FROM users u WHERE ' + ' AND '.join(conditions),
            params,
        ).fetchone()
        return int(row['cnt'] if row else 0)

def get_expiring_keys(days: int) -> List[Dict[str, Any]]:
    """
    Retrieves keys that will expire in the next N days (but have not yet expired).
    
    Args:
        days: Number of days until expiration
    
    Returns:
        List of dictionaries: vpn_key_id, user_telegram_id, expires_at, custom_name, days_left
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 
                vk.id as vpn_key_id,
                u.telegram_id as user_telegram_id,
                vk.expires_at,
                vk.custom_name,
                CAST((julianday(vk.expires_at) - julianday('now')) AS INTEGER) as days_left
            FROM vpn_keys vk
            JOIN users u ON vk.user_id = u.id
            WHERE u.is_banned = 0
            AND u.is_bot_blocked = 0
            AND vk.expires_at IS NOT NULL
            AND vk.expires_at > datetime('now')
            AND vk.expires_at <= datetime('now', '+' || ? || ' days')
        """, (days,))
        return [dict(row) for row in cursor.fetchall()]

def is_notification_sent_today(vpn_key_id: int) -> bool:
    """
    Checks whether a notification was sent for this key today.
    
    Args:
        vpn_key_id: VPN key ID
    
    Returns:
        True if the notification has already been sent today
    """
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT 1 FROM notification_log
            WHERE vpn_key_id = ? AND sent_at = date('now')
        """, (vpn_key_id,))
        return cursor.fetchone() is not None

def log_notification_sent(vpn_key_id: int) -> None:
    """
    Records the fact that a notification was sent.
    
    Args:
        vpn_key_id: VPN key ID
    """
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO notification_log (vpn_key_id, sent_at)
            VALUES (?, date('now'))
        """, (vpn_key_id,))
        logger.debug(f"Записано уведомление для ключа {vpn_key_id}")

def get_keys_stats() -> Dict[str, int]:
    """
    Gets VPN key statistics.
    
    Returns:
        Dictionary with statistics:
        - total: total keys
        - active: active (not expired)
        - expired: expired
        - created_today: created in the last 24 hours
    """
    with get_db() as conn:
        # Total keys
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM vpn_keys")
        total = cursor.fetchone()['cnt']
        
        # Active (not expired)
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM vpn_keys 
            WHERE expires_at > datetime('now') OR expires_at IS NULL
        """)
        active = cursor.fetchone()['cnt']
        
        # Created per day
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM vpn_keys 
            WHERE created_at >= datetime('now', '-1 day')
        """)
        created_today = cursor.fetchone()['cnt']
        
        return {
            'total': total,
            'active': active,
            'expired': total - active,
            'created_today': created_today
        }
