"""Database contract for tariff-backed trial offers and activations."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from .connection import get_db
from .db_keys import _create_initial_vpn_key_with_conn
from .db_payments import _complete_order_with_conn, _create_pending_order_with_conn

logger = logging.getLogger(__name__)

TRIAL_SCOPE_ONCE_PER_USER = 'once_per_user'
TRIAL_SCOPE_ONCE_PER_GROUP = 'once_per_group'
TRIAL_USAGE_SCOPES = frozenset({
    TRIAL_SCOPE_ONCE_PER_USER,
    TRIAL_SCOPE_ONCE_PER_GROUP,
})
TRIAL_OFFER_ACTION_PREFIX = 'cmd_trial_offer:'

__all__ = [
    'TRIAL_SCOPE_ONCE_PER_USER',
    'TRIAL_SCOPE_ONCE_PER_GROUP',
    'TRIAL_USAGE_SCOPES',
    'TRIAL_OFFER_ACTION_PREFIX',
    'get_trial_usage_scope',
    'set_trial_usage_scope',
    'is_trial_offer_storage_ready',
    'get_all_trial_offers',
    'get_trial_offer_by_id',
    'get_primary_trial_offer',
    'get_trial_offer_eligibility',
    'get_primary_trial_eligibility',
    'can_use_primary_trial',
    'set_primary_trial_enabled',
    'set_primary_trial_tariff',
    'create_trial_offer',
    'update_trial_offer',
    'delete_trial_offer',
    'trial_offer_action_value',
    'claim_trial_offer',
]


_TRIAL_OFFER_SELECT = """
    SELECT
        o.id AS offer_id,
        o.tariff_id,
        o.is_primary,
        o.is_enabled,
        o.created_at,
        o.updated_at,
        t.id AS resolved_tariff_id,
        t.name AS tariff_name,
        t.duration_days,
        t.traffic_limit_gb,
        t.max_ips,
        t.is_active AS tariff_is_active,
        t.system_type,
        t.group_id,
        tg.name AS group_name
    FROM trial_offers o
    LEFT JOIN tariffs t ON t.id = o.tariff_id
    LEFT JOIN tariff_groups tg ON tg.id = t.group_id
"""


def _normalize_scope(value: Any) -> str:
    scope = str(value or '').strip().casefold()
    return scope if scope in TRIAL_USAGE_SCOPES else TRIAL_SCOPE_ONCE_PER_USER


def _scope_with_conn(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'trial_usage_scope'"
    ).fetchone()
    return _normalize_scope(row['value'] if row else None)


def get_trial_usage_scope() -> str:
    """Returns the current eligibility scope with a safe compatibility default."""
    with get_db() as conn:
        return _scope_with_conn(conn)


def set_trial_usage_scope(scope: str) -> bool:
    """Sets the hidden eligibility scope after strict enum validation."""
    normalized = str(scope or '').strip().casefold()
    if normalized not in TRIAL_USAGE_SCOPES:
        raise ValueError(
            f"trial usage scope must be one of {sorted(TRIAL_USAGE_SCOPES)}"
        )
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('trial_usage_scope', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (normalized,),
        )
    return True


def is_trial_offer_storage_ready() -> bool:
    """Returns whether the complete v93 trial schema is available."""
    required = {
        'tariffs',
        'tariff_groups',
        'trial_offers',
        'trial_activations',
    }
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                    'tariffs', 'tariff_groups',
                    'trial_offers', 'trial_activations'
              )
            """
        ).fetchall()
    return {str(row['name']) for row in rows} == required


def _offer_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _get_offer_with_conn(
    conn: sqlite3.Connection,
    offer_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        _TRIAL_OFFER_SELECT + " WHERE o.id = ?",
        (int(offer_id),),
    ).fetchone()
    return _offer_from_row(row)


def _get_primary_offer_with_conn(
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = conn.execute(
        _TRIAL_OFFER_SELECT + " WHERE o.is_primary = 1 LIMIT 1"
    ).fetchone()
    return _offer_from_row(row)


def get_all_trial_offers() -> list[dict[str, Any]]:
    """Returns primary and additional offers with their current tariff group."""
    with get_db() as conn:
        rows = conn.execute(
            _TRIAL_OFFER_SELECT + " ORDER BY o.is_primary DESC, o.id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_trial_offer_by_id(offer_id: int) -> dict[str, Any] | None:
    """Returns one trial offer, including disabled and currently invalid rows."""
    with get_db() as conn:
        return _get_offer_with_conn(conn, int(offer_id))


def get_primary_trial_offer() -> dict[str, Any] | None:
    """Returns the protected primary trial offer."""
    with get_db() as conn:
        return _get_primary_offer_with_conn(conn)


def _offer_target_reason(offer: dict[str, Any] | None) -> str | None:
    if offer is None:
        return 'offer_not_found'
    if not bool(offer.get('is_enabled')):
        return 'offer_disabled'
    if offer.get('resolved_tariff_id') is None or offer.get('group_id') is None:
        return 'tariff_unavailable'
    if offer.get('system_type') is not None:
        return 'system_tariff_forbidden'
    return None


def _eligibility_with_conn(
    conn: sqlite3.Connection,
    offer: dict[str, Any] | None,
    *,
    internal_user_id: int | None,
) -> dict[str, Any]:
    scope = _scope_with_conn(conn)
    reason = _offer_target_reason(offer)
    result = {
        'eligible': reason is None,
        'reason': reason,
        'scope': scope,
        'offer': offer,
    }
    if reason is not None or internal_user_id is None:
        return result

    legacy = conn.execute(
        """
        SELECT 1
        FROM trial_activations
        WHERE user_id = ? AND legacy_global_block = 1
        LIMIT 1
        """,
        (int(internal_user_id),),
    ).fetchone()
    if legacy is not None:
        result.update(eligible=False, reason='legacy_trial_used')
        return result

    if scope == TRIAL_SCOPE_ONCE_PER_USER:
        used = conn.execute(
            """
            SELECT 1
            FROM trial_activations
            WHERE user_id = ?
            LIMIT 1
            """,
            (int(internal_user_id),),
        ).fetchone()
        if used is not None:
            result.update(eligible=False, reason='trial_used')
        return result

    used = conn.execute(
        """
        SELECT 1
        FROM trial_activations
        WHERE user_id = ?
          AND legacy_global_block = 0
          AND group_id = ?
        LIMIT 1
        """,
        (int(internal_user_id), int(offer['group_id'])),
    ).fetchone()
    if used is not None:
        result.update(eligible=False, reason='group_trial_used')
    return result


def get_trial_offer_eligibility(
    telegram_id: int | None,
    offer_id: int,
) -> dict[str, Any]:
    """Checks an offer for a Telegram user without mutating eligibility state."""
    with get_db() as conn:
        offer = _get_offer_with_conn(conn, int(offer_id))
        internal_user_id = None
        if telegram_id is not None:
            row = conn.execute(
                "SELECT id FROM users WHERE telegram_id = ?",
                (int(telegram_id),),
            ).fetchone()
            internal_user_id = int(row['id']) if row else None
        return _eligibility_with_conn(
            conn,
            offer,
            internal_user_id=internal_user_id,
        )


def get_primary_trial_eligibility(
    telegram_id: int | None,
) -> dict[str, Any]:
    """Checks the protected primary offer for one Telegram user."""
    with get_db() as conn:
        offer = _get_primary_offer_with_conn(conn)
        internal_user_id = None
        if telegram_id is not None:
            row = conn.execute(
                "SELECT id FROM users WHERE telegram_id = ?",
                (int(telegram_id),),
            ).fetchone()
            internal_user_id = int(row['id']) if row else None
        return _eligibility_with_conn(
            conn,
            offer,
            internal_user_id=internal_user_id,
        )


def can_use_primary_trial(telegram_id: int | None) -> bool:
    """Returns whether the primary offer should be visible to one user."""
    return bool(get_primary_trial_eligibility(telegram_id).get('eligible'))


def _require_normal_tariff(conn: sqlite3.Connection, tariff_id: int) -> None:
    row = conn.execute(
        "SELECT id, system_type FROM tariffs WHERE id = ?",
        (int(tariff_id),),
    ).fetchone()
    if row is None:
        raise ValueError('trial tariff does not exist')
    if row['system_type'] is not None:
        raise ValueError('system tariffs cannot be used for trial offers')


def set_primary_trial_enabled(enabled: bool) -> bool:
    """Enables or disables only the protected primary offer."""
    if not isinstance(enabled, bool):
        raise TypeError('enabled must be bool')
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE trial_offers
            SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE is_primary = 1
            """,
            (int(enabled),),
        )
        return cursor.rowcount > 0


def set_primary_trial_tariff(tariff_id: int) -> bool:
    """Changes the tariff of the protected primary offer."""
    with get_db() as conn:
        _require_normal_tariff(conn, int(tariff_id))
        cursor = conn.execute(
            """
            UPDATE trial_offers
            SET tariff_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE is_primary = 1
            """,
            (int(tariff_id),),
        )
        return cursor.rowcount > 0


def create_trial_offer(tariff_id: int, *, enabled: bool = True) -> int:
    """Creates one additional trial offer."""
    if not isinstance(enabled, bool):
        raise TypeError('enabled must be bool')
    with get_db() as conn:
        _require_normal_tariff(conn, int(tariff_id))
        cursor = conn.execute(
            """
            INSERT INTO trial_offers (tariff_id, is_primary, is_enabled)
            VALUES (?, 0, ?)
            """,
            (int(tariff_id), int(enabled)),
        )
        return int(cursor.lastrowid)


def update_trial_offer(
    offer_id: int,
    *,
    tariff_id: int | None = None,
    enabled: bool | None = None,
) -> bool:
    """Updates one additional offer without exposing the primary marker."""
    if tariff_id is None and enabled is None:
        return False
    if enabled is not None and not isinstance(enabled, bool):
        raise TypeError('enabled must be bool')
    with get_db() as conn:
        row = conn.execute(
            "SELECT is_primary FROM trial_offers WHERE id = ?",
            (int(offer_id),),
        ).fetchone()
        if row is None or bool(row['is_primary']):
            return False
        updates: list[str] = []
        values: list[Any] = []
        if tariff_id is not None:
            _require_normal_tariff(conn, int(tariff_id))
            updates.append('tariff_id = ?')
            values.append(int(tariff_id))
        if enabled is not None:
            updates.append('is_enabled = ?')
            values.append(int(enabled))
        updates.append('updated_at = CURRENT_TIMESTAMP')
        values.append(int(offer_id))
        cursor = conn.execute(
            f"UPDATE trial_offers SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        return cursor.rowcount > 0


def delete_trial_offer(offer_id: int) -> bool:
    """Deletes an additional offer while preserving activation snapshots."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM trial_offers WHERE id = ? AND is_primary = 0",
            (int(offer_id),),
        )
        return cursor.rowcount > 0


def trial_offer_action_value(offer_id: int) -> str:
    """Builds the stable page-button action for one persisted offer."""
    normalized = int(offer_id)
    if normalized <= 0:
        raise ValueError('offer_id must be positive')
    return f'{TRIAL_OFFER_ACTION_PREFIX}{normalized}'


def claim_trial_offer(user_id: int, offer_id: int) -> dict[str, Any]:
    """Atomically consumes eligibility and creates a paid draft trial order."""
    normalized_user_id = int(user_id)
    normalized_offer_id = int(offer_id)
    if normalized_user_id <= 0 or normalized_offer_id <= 0:
        raise ValueError('user_id and offer_id must be positive')

    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute(
            "SELECT id FROM users WHERE id = ?",
            (normalized_user_id,),
        ).fetchone()
        if user is None:
            return {'ok': False, 'reason': 'user_not_found'}

        offer = _get_offer_with_conn(conn, normalized_offer_id)
        eligibility = _eligibility_with_conn(
            conn,
            offer,
            internal_user_id=normalized_user_id,
        )
        if not eligibility['eligible']:
            return {
                'ok': False,
                'reason': eligibility['reason'],
                'scope': eligibility['scope'],
                'offer': offer,
            }

        try:
            activation_cursor = conn.execute(
                """
                INSERT INTO trial_activations (
                    user_id, offer_id, tariff_id, group_id,
                    legacy_global_block
                ) VALUES (?, ?, ?, ?, 0)
                """,
                (
                    normalized_user_id,
                    normalized_offer_id,
                    int(offer['tariff_id']),
                    int(offer['group_id']),
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return {
                'ok': False,
                'reason': 'group_trial_used',
                'scope': eligibility['scope'],
                'offer': offer,
            }

        duration_days = max(0, int(offer.get('duration_days') or 0))
        traffic_limit = max(0, int(offer.get('traffic_limit_gb') or 0)) * 1024 ** 3
        key_id = _create_initial_vpn_key_with_conn(
            conn,
            normalized_user_id,
            int(offer['tariff_id']),
            duration_days,
            traffic_limit,
        )
        payment_id, order_id = _create_pending_order_with_conn(
            conn,
            normalized_user_id,
            int(offer['tariff_id']),
            'trial',
            key_id,
        )
        if not _complete_order_with_conn(conn, order_id):
            raise RuntimeError('failed to complete atomic trial order')

        activation_id = int(activation_cursor.lastrowid)
        conn.execute(
            """
            UPDATE trial_activations
            SET vpn_key_id = ?, payment_id = ?
            WHERE id = ?
            """,
            (key_id, payment_id, activation_id),
        )
        conn.execute(
            "UPDATE users SET used_trial = 1 WHERE id = ?",
            (normalized_user_id,),
        )

        logger.info(
            "Trial offer %s claimed by user %s: key=%s order=%s group=%s",
            normalized_offer_id,
            normalized_user_id,
            key_id,
            order_id,
            offer['group_id'],
        )
        return {
            'ok': True,
            'activation_id': activation_id,
            'key_id': key_id,
            'payment_id': payment_id,
            'order_id': order_id,
            'scope': eligibility['scope'],
            'offer': offer,
        }
