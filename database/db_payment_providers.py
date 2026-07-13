"""Storage of connections between core orders and custom payment providers."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional

from .connection import get_db

_ALLOWED_PROVIDER_ORDER_STATUSES = {'pending', 'succeeded', 'canceled'}


def create_payment_provider_support_tables(conn: sqlite3.Connection) -> None:
    """Creates a system table of custom payment providers."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_provider_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL UNIQUE,
            provider_id TEXT NOT NULL,
            payment_type TEXT NOT NULL,
            provider_payment_id TEXT,
            payment_url TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_provider
        ON payment_provider_orders(provider_id, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_provider_orders_external
        ON payment_provider_orders(provider_id, provider_payment_id)
        """
    )


def save_payment_provider_order(
    *,
    order_id: str,
    provider_id: str,
    payment_type: str,
    provider_payment_id: str | None = None,
    payment_url: str | None = None,
    status: str = 'pending',
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Saves or updates the external payment of a custom provider."""
    normalized_status = _normalize_status(status)
    metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False)
    with get_db() as conn:
        create_payment_provider_support_tables(conn)
        cursor = conn.execute(
            """
            INSERT INTO payment_provider_orders (
                order_id, provider_id, payment_type, provider_payment_id,
                payment_url, status, metadata_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(order_id) DO UPDATE SET
                provider_id = excluded.provider_id,
                payment_type = excluded.payment_type,
                provider_payment_id = excluded.provider_payment_id,
                payment_url = excluded.payment_url,
                status = excluded.status,
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                order_id,
                provider_id,
                payment_type,
                provider_payment_id,
                payment_url,
                normalized_status,
                metadata_json,
            ),
        )
        return cursor.rowcount > 0


def get_payment_provider_order(order_id: str) -> Optional[dict[str, Any]]:
    """Returns a custom provider record by core order_id."""
    with get_db() as conn:
        create_payment_provider_support_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM payment_provider_orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        return _row_to_dict(row)


def find_payment_provider_order_by_external_id(
    provider_id: str,
    provider_payment_id: str,
) -> Optional[dict[str, Any]]:
    """Searches for a connection by payment id on the provider side."""
    with get_db() as conn:
        create_payment_provider_support_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM payment_provider_orders
            WHERE provider_id = ? AND provider_payment_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (provider_id, provider_payment_id),
        ).fetchone()
        return _row_to_dict(row)


def get_open_payment_provider_orders(limit: int = 50) -> list[dict[str, Any]]:
    """Returns provider-orders that can still close the pending core order."""
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError):
        normalized_limit = 50
    normalized_limit = max(1, min(normalized_limit, 500))

    with get_db() as conn:
        create_payment_provider_support_tables(conn)
        rows = conn.execute(
            """
            SELECT ppo.*
            FROM payment_provider_orders ppo
            JOIN payments p ON p.order_id = ppo.order_id
            WHERE p.status = 'pending'
              AND ppo.status IN ('pending', 'succeeded')
            ORDER BY
              CASE ppo.status WHEN 'succeeded' THEN 0 ELSE 1 END,
              ppo.updated_at ASC,
              ppo.id ASC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def update_payment_provider_order_status(
    order_id: str,
    status: str,
    *,
    provider_payment_id: str | None = None,
    payment_url: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Updates the status of a custom payment."""
    normalized_status = _normalize_status(status)
    metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False) if metadata is not None else None
    with get_db() as conn:
        create_payment_provider_support_tables(conn)
        cursor = conn.execute(
            """
            UPDATE payment_provider_orders
            SET status = ?,
                provider_payment_id = COALESCE(?, provider_payment_id),
                payment_url = COALESCE(?, payment_url),
                metadata_json = COALESCE(?, metadata_json),
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ?
            """,
            (normalized_status, provider_payment_id, payment_url, metadata_json, order_id),
        )
        return cursor.rowcount > 0


def _normalize_status(status: str) -> str:
    value = str(status or '').strip().casefold()
    if value not in _ALLOWED_PROVIDER_ORDER_STATUSES:
        raise ValueError('status должен быть pending, succeeded или canceled')
    return value


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    metadata = data.pop('metadata_json', None)
    try:
        data['metadata'] = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError:
        data['metadata'] = {}
    return data


__all__ = [
    'create_payment_provider_support_tables',
    'find_payment_provider_order_by_external_id',
    'get_payment_provider_order',
    'get_open_payment_provider_orders',
    'save_payment_provider_order',
    'update_payment_provider_order_status',
]
