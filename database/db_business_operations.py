"""Business history of key and balance transactions."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .connection import get_db

logger = logging.getLogger(__name__)

__all__ = [
    'apply_balance_operation',
    'create_business_operation_tables',
    'get_first_active_key_for_user',
    'has_balance_operation_reference',
    'get_key_operation_history',
    'record_key_operation',
]

_BALANCE_OPERATION_TYPES = {'credit', 'debit'}


def has_balance_operation_reference(
    *,
    user_id: int,
    operation_type: str,
    source: str,
    reference_type: str,
    reference_id: str,
) -> bool:
    """Checks whether an idempotent balance side effect was already recorded."""
    with get_db() as conn:
        create_business_operation_tables(conn)
        row = conn.execute(
            """
            SELECT 1
            FROM balance_operations
            WHERE user_id = ?
              AND operation_type = ?
              AND source = ?
              AND reference_type = ?
              AND reference_id = ?
            LIMIT 1
            """,
            (
                int(user_id),
                str(operation_type),
                str(source),
                str(reference_type),
                str(reference_id),
            ),
        ).fetchone()
        return row is not None


def create_business_operation_tables(conn: sqlite3.Connection) -> None:
    """Creates regular logs of kernel business operations."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS key_operation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vpn_key_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_days INTEGER DEFAULT 0,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            expires_before TEXT,
            expires_after TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_operation_log_key_created
        ON key_operation_log(vpn_key_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_key_operation_log_user_created
        ON key_operation_log(user_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            delta_cents INTEGER NOT NULL,
            balance_before INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            source TEXT NOT NULL,
            reason TEXT,
            reference_type TEXT,
            reference_id TEXT,
            performed_by INTEGER,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_operations_user_created
        ON balance_operations(user_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_operations_reference
        ON balance_operations(reference_type, reference_id)
        """
    )


def get_first_active_key_for_user(user_id: int) -> dict[str, Any] | None:
    """Returns the user's first active key for domain accrual days."""
    with get_db() as conn:
        cursor = conn.execute(
            """
            SELECT *
            FROM vpn_keys
            WHERE user_id = ? AND expires_at > datetime('now')
            ORDER BY expires_at DESC
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def record_key_operation(
    *,
    key_id: int,
    user_id: int,
    operation_type: str,
    delta_days: int = 0,
    source: str,
    reason: str | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    expires_before: str | None = None,
    expires_after: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Writes a visible business history of the key transaction."""
    with get_db() as conn:
        create_business_operation_tables(conn)
        cursor = conn.execute(
            """
            INSERT INTO key_operation_log (
                vpn_key_id, user_id, operation_type, delta_days, source,
                reason, reference_type, reference_id,
                expires_before, expires_after, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _positive_int(key_id, 'key_id'),
                _positive_int(user_id, 'user_id'),
                _text(operation_type, 'operation_type'),
                int(delta_days or 0),
                _text(source, 'source'),
                _optional_text(reason),
                _optional_text(reference_type),
                _optional_text(reference_id),
                _optional_text(expires_before),
                _optional_text(expires_after),
                _json_metadata(metadata or {}),
            ),
        )
        operation_id = int(cursor.lastrowid)
        logger.info(
            "Записана операция ключа id=%s key=%s source=%s days=%s",
            operation_id,
            key_id,
            source,
            delta_days,
        )
        return operation_id


def get_key_operation_history(key_id: int) -> list[dict[str, Any]]:
    """Returns the non-payment transaction history of the key."""
    with get_db() as conn:
        create_business_operation_tables(conn)
        rows = conn.execute(
            """
            SELECT
                id,
                created_at AS paid_at,
                operation_type,
                delta_days,
                source AS operation_source,
                reason,
                reference_type,
                reference_id,
                expires_before,
                expires_after,
                metadata,
                'key_operation' AS history_type
            FROM key_operation_log
            WHERE vpn_key_id = ?
            ORDER BY created_at DESC
            """,
            (_positive_int(key_id, 'key_id'),),
        ).fetchall()
        return [dict(row) for row in rows]


def apply_balance_operation(
    *,
    user_id: int,
    operation_type: str,
    cents: int,
    source: str,
    reason: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    performed_by: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically changes the user's balance and writes a regular history."""
    user_id = _positive_int(user_id, 'user_id')
    operation = _balance_operation_type(operation_type)
    amount = _positive_int(cents, 'cents')
    source_text = _text(source, 'source')
    reason_text = _text(reason, 'reason')

    with get_db() as conn:
        create_business_operation_tables(conn)
        row = conn.execute(
            "SELECT personal_balance FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {'ok': False, 'status': 'user_not_found'}

        before = int(row['personal_balance'] or 0)
        delta = amount if operation == 'credit' else -amount
        after = before + delta
        if after < 0:
            return {
                'ok': False,
                'status': 'insufficient_funds',
                'balance_before': before,
                'balance_after': before,
                'delta_cents': 0,
            }

        cursor = conn.execute(
            "UPDATE users SET personal_balance = ? WHERE id = ?",
            (after, user_id),
        )
        if cursor.rowcount <= 0:
            return {'ok': False, 'status': 'user_not_found'}

        history = conn.execute(
            """
            INSERT INTO balance_operations (
                user_id, operation_type, delta_cents,
                balance_before, balance_after, source, reason,
                reference_type, reference_id, performed_by, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                operation,
                delta,
                before,
                after,
                source_text,
                reason_text,
                _optional_text(reference_type),
                _optional_text(reference_id),
                performed_by if performed_by is None else _positive_int(performed_by, 'performed_by'),
                _json_metadata(metadata or {}),
            ),
        )
        operation_id = int(history.lastrowid)
        logger.info(
            "Баланс user=%s изменён на %s коп source=%s operation_id=%s",
            user_id,
            delta,
            source_text,
            operation_id,
        )
        return {
            'ok': True,
            'status': 'applied',
            'operation_id': operation_id,
            'user_id': user_id,
            'operation_type': operation,
            'delta_cents': delta,
            'balance_before': before,
            'balance_after': after,
        }


def _balance_operation_type(value: Any) -> str:
    operation = _text(value, 'operation_type')
    if operation not in _BALANCE_OPERATION_TYPES:
        raise ValueError('operation_type должен быть credit или debit')
    return operation


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} должен быть положительным integer')
    return int(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{field} должен быть строкой')
    text = value.strip()
    if not text:
        raise ValueError(f'{field} не может быть пустым')
    if len(text) > 256:
        raise ValueError(f'{field} не может быть длиннее 256 символов')
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('опциональное текстовое поле должно быть строкой')
    text = value.strip()
    return text[:256] if text else None


def _json_metadata(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
