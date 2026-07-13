"""Idempotency log core commands available with custom extensions via facade."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from .connection import get_db
from .db_extensions import normalize_extension_id

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_RE = re.compile(r'^[a-zA-Z0-9_.:-]{1,128}$')
_ALLOWED_OPERATIONS = {'grant_days_to_first_active_key', 'add_balance_bonus'}
_PUBLIC_STATUSES = {'pending', 'applied', 'already_applied', 'no_op', 'rejected', 'failed'}


def create_extension_core_operation_table(conn: sqlite3.Connection) -> None:
    """Creates a log of idempotency extension facade commands."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_core_operations (
            extension_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            target_user_id INTEGER,
            amount INTEGER,
            reason TEXT,
            status TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (extension_id, idempotency_key)
        )
        """
    )


def claim_extension_core_operation(
    *,
    extension_id: str,
    idempotency_key: str,
    operation: str,
    target_user_id: int,
    amount: int,
    reason: str,
) -> dict[str, Any]:
    """
    Registers a facade command as pending or returns an existing result.

    The DB layer here does not change domain tables: it is only responsible for
    idempotency and diagnostics of facade commands.
    """
    ext_id = normalize_extension_id(extension_id)
    key = _normalize_idempotency_key(idempotency_key)
    op = _normalize_operation(operation)
    user_id = _normalize_positive_int(target_user_id, 'target_user_id')
    value = _normalize_positive_int(amount, 'amount')
    reason_text = _normalize_reason(reason)

    with get_db() as conn:
        create_extension_core_operation_table(conn)
        existing = _get_operation_row(conn, ext_id, key)
        if existing is not None:
            result = _row_result(existing, already_applied=True)
            result['claimed'] = False
            return result

        conn.execute(
            """
            INSERT INTO extension_core_operations (
                extension_id, idempotency_key, operation, target_user_id,
                amount, reason, status, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                ext_id,
                key,
                op,
                user_id,
                value,
                reason_text,
                _json_metadata({'pending': True}),
            ),
        )
        row = _get_operation_row(conn, ext_id, key)
        result = _row_result(row, already_applied=False)
        result['claimed'] = True
        return result


def finalize_extension_core_operation(
    *,
    extension_id: str,
    idempotency_key: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fixes the result of an already declared facade command."""
    ext_id = normalize_extension_id(extension_id)
    key = _normalize_idempotency_key(idempotency_key)
    status_value = _normalize_status(status)
    metadata_value = _json_metadata(metadata or {})

    with get_db() as conn:
        create_extension_core_operation_table(conn)
        cursor = conn.execute(
            """
            UPDATE extension_core_operations
            SET status = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
            WHERE extension_id = ? AND idempotency_key = ?
            """,
            (status_value, metadata_value, ext_id, key),
        )
        if cursor.rowcount <= 0:
            return {
                'ok': False,
                'applied': False,
                'already_applied': False,
                'status': 'failed',
                'metadata': {'reason': 'operation_not_claimed'},
            }
        row = _get_operation_row(conn, ext_id, key)
        logger.info(
            "Extension core operation %s:%s finalized status=%s",
            ext_id,
            key,
            status_value,
        )
        return _row_result(row, already_applied=False)


def get_extension_core_operation(
    *,
    extension_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """Returns the log entry of the facade command."""
    ext_id = normalize_extension_id(extension_id)
    key = _normalize_idempotency_key(idempotency_key)
    with get_db() as conn:
        create_extension_core_operation_table(conn)
        row = _get_operation_row(conn, ext_id, key)
        return _row_result(row, already_applied=False) if row else None


def _get_operation_row(
    conn: sqlite3.Connection,
    extension_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM extension_core_operations
        WHERE extension_id = ? AND idempotency_key = ?
        """,
        (extension_id, idempotency_key),
    ).fetchone()


def _row_result(row: sqlite3.Row | None, *, already_applied: bool) -> dict[str, Any]:
    if row is None:
        return {
            'ok': False,
            'applied': False,
            'already_applied': already_applied,
            'status': 'failed',
            'metadata': {'reason': 'missing'},
        }
    metadata = {}
    if row['metadata']:
        try:
            metadata = json.loads(row['metadata'])
        except json.JSONDecodeError:
            metadata = {}

    stored_status = str(row['status'])
    public_status = 'already_applied' if already_applied and stored_status == 'applied' else stored_status
    return {
        'ok': stored_status == 'applied',
        'applied': stored_status == 'applied' and not already_applied,
        'already_applied': already_applied,
        'status': public_status,
        'stored_status': stored_status,
        'operation': row['operation'],
        'target_user_id': row['target_user_id'],
        'amount': row['amount'],
        'reason': row['reason'],
        'metadata': metadata,
    }


def _normalize_idempotency_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('idempotency_key должен быть строкой')
    key = value.strip()
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError('idempotency_key содержит недопустимые символы')
    return key


def _normalize_operation(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('operation должен быть строкой')
    op = value.strip()
    if op not in _ALLOWED_OPERATIONS:
        raise ValueError(f"operation {op!r} не разрешена")
    return op


def _normalize_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{field} должен быть положительным integer')
    return value


def _normalize_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('reason должен быть строкой')
    reason = value.strip()
    if not reason:
        raise ValueError('reason не может быть пустым')
    if len(reason) > 256:
        raise ValueError('reason не может быть длиннее 256 символов')
    return reason


def _normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('status должен быть строкой')
    status = value.strip()
    if status not in _PUBLIC_STATUSES:
        raise ValueError(f"status {status!r} не разрешён")
    return status


def _json_metadata(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


__all__ = [
    'claim_extension_core_operation',
    'create_extension_core_operation_table',
    'finalize_extension_core_operation',
    'get_extension_core_operation',
]
