"""Persistent scheduling state for bounded payment provider polling."""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .connection import get_db


AUTO_CHECK_STATES = {
    'active',
    'provider_succeeded',
    'completed',
    'canceled',
    'exhausted',
    'completion_failed',
}


def create_payment_auto_check_tables(conn: sqlite3.Connection) -> None:
    """Creates payment auto-check storage for tests and compatibility guards."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_auto_checks (
            order_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            next_check_at TIMESTAMP,
            last_check_at TIMESTAMP,
            check_attempts INTEGER NOT NULL DEFAULT 0,
            completion_attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES payments(order_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_payment_auto_checks_due
        ON payment_auto_checks(state, next_check_at)
        """
    )


def schedule_payment_auto_check(
    order_id: str,
    provider_id: str,
    *,
    first_delay_seconds: int = 120,
) -> bool:
    """Starts bounded polling for a new external payment."""
    delay = max(0, int(first_delay_seconds))
    modifier = f'+{delay} seconds'
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        cursor = conn.execute(
            """
            INSERT INTO payment_auto_checks (
                order_id, provider_id, state, next_check_at, last_error, updated_at
            )
            VALUES (?, ?, 'active', datetime('now', ?), NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(order_id) DO UPDATE SET
                provider_id = excluded.provider_id,
                state = 'active',
                started_at = CURRENT_TIMESTAMP,
                next_check_at = excluded.next_check_at,
                last_check_at = NULL,
                check_attempts = 0,
                completion_attempts = 0,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (str(order_id), str(provider_id), modifier),
        )
        return cursor.rowcount > 0


def get_payment_auto_check(order_id: str) -> Optional[dict[str, Any]]:
    """Returns polling state for one core order."""
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        row = conn.execute(
            "SELECT * FROM payment_auto_checks WHERE order_id = ?",
            (str(order_id),),
        ).fetchone()
        return dict(row) if row else None


def get_due_payment_auto_checks(limit: int = 10) -> list[dict[str, Any]]:
    """Returns due active checks and successful providers awaiting completion."""
    normalized_limit = max(1, min(int(limit), 100))
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        rows = conn.execute(
            """
            SELECT pac.*, p.payment_type, p.status AS order_status,
                   p.user_id, p.vpn_key_id, p.final_amount_cents,
                   p.amount_cents, p.balance_deduct_cents,
                   p.yookassa_payment_id, p.wata_link_id,
                   p.platega_transaction_id, p.cardlink_bill_id
            FROM payment_auto_checks pac
            JOIN payments p ON p.order_id = pac.order_id
            WHERE (
                    (p.status = 'pending' AND pac.state IN ('active', 'provider_succeeded'))
                 OR (p.status = 'paid' AND pac.state = 'provider_succeeded')
              )
              AND pac.next_check_at IS NOT NULL
              AND pac.next_check_at <= CURRENT_TIMESTAMP
            ORDER BY
              CASE pac.state WHEN 'provider_succeeded' THEN 0 ELSE 1 END,
              pac.next_check_at ASC,
              pac.order_id ASC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def record_payment_auto_check_attempt(order_id: str) -> bool:
    """Records one scheduled provider-status check, independently of HTTP retries."""
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        cursor = conn.execute(
            """
            UPDATE payment_auto_checks
            SET check_attempts = check_attempts + 1,
                last_check_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ? AND state = 'active'
            """,
            (str(order_id),),
        )
        return cursor.rowcount > 0


def update_payment_auto_check(
    order_id: str,
    *,
    state: str,
    next_delay_seconds: int | None = None,
    last_error: str | None = None,
) -> bool:
    """Updates polling state and optionally schedules its next run."""
    normalized_state = str(state or '').strip().casefold()
    if normalized_state not in AUTO_CHECK_STATES:
        raise ValueError(f'Unsupported payment auto-check state: {state}')
    modifier = None
    if next_delay_seconds is not None:
        modifier = f'+{max(0, int(next_delay_seconds))} seconds'
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        cursor = conn.execute(
            """
            UPDATE payment_auto_checks
            SET state = ?,
                next_check_at = CASE
                    WHEN ? IS NULL THEN NULL
                    ELSE datetime('now', ?)
                END,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ?
            """,
            (normalized_state, modifier, modifier, last_error, str(order_id)),
        )
        return cursor.rowcount > 0


def record_payment_completion_attempt(
    order_id: str,
    *,
    next_delay_seconds: int | None = None,
    last_error: str | None = None,
) -> int:
    """Records a background completion attempt and returns the new count."""
    modifier = None
    if next_delay_seconds is not None:
        modifier = f'+{max(0, int(next_delay_seconds))} seconds'
    with get_db() as conn:
        create_payment_auto_check_tables(conn)
        conn.execute(
            """
            UPDATE payment_auto_checks
            SET completion_attempts = completion_attempts + 1,
                next_check_at = CASE
                    WHEN ? IS NULL THEN next_check_at
                    ELSE datetime('now', ?)
                END,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_id = ? AND state = 'provider_succeeded'
            """,
            (modifier, modifier, last_error, str(order_id)),
        )
        row = conn.execute(
            "SELECT completion_attempts FROM payment_auto_checks WHERE order_id = ?",
            (str(order_id),),
        ).fetchone()
        return int(row['completion_attempts'] or 0) if row else 0


__all__ = [
    'AUTO_CHECK_STATES',
    'create_payment_auto_check_tables',
    'get_due_payment_auto_checks',
    'get_payment_auto_check',
    'record_payment_auto_check_attempt',
    'record_payment_completion_attempt',
    'schedule_payment_auto_check',
    'update_payment_auto_check',
]
