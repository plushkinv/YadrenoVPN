"""Transactional event outbox and independently leased subscriber deliveries."""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .connection import get_db
from .db_extension_payments import iso_utc, payment_with_conn, summary_with_conn
from .db_extension_deliveries import (
    DELIVERY_DUE_SQL, claim_delivery_with_conn, extension_delivery_diagnostics,
    finish_extension_delivery,
)

__all__ = [
    'get_due_core_event_delivery_ids', 'claim_core_event_delivery',
    'finish_core_event_delivery', 'get_core_event_diagnostics',
    'record_key_delivery_event',
]


def create_core_event_tables(conn) -> None:
    """Called only from the core migration."""
    conn.execute('''CREATE TABLE IF NOT EXISTS core_events (
        event_id TEXT PRIMARY KEY,
        event_name TEXT NOT NULL,
        source_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        UNIQUE(event_name, source_id)
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS extension_event_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL REFERENCES core_events(event_id),
        extension_id TEXT NOT NULL,
        handler_name TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'processing', 'completed', 'degraded')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        lease_until TEXT,
        claim_token TEXT,
        last_error_code TEXT,
        completed_at TEXT,
        UNIQUE(event_id, extension_id, handler_name)
    )''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_extension_events_due
        ON extension_event_deliveries(state, next_attempt_at, lease_until, id)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_extension_events_handler
        ON extension_event_deliveries(extension_id, handler_name, state)''')


def record_core_event_with_conn(
    conn, *, event_name: str, source_id: str, order_id: str | None = None,
    subscribers=(), trial: dict[str, Any] | None = None,
    expired_key: dict[str, Any] | None = None,
    registered_user_id: int | None = None,
) -> str:
    """Capture the operation and relevant user facts inside its write transaction."""
    existing = conn.execute(
        'SELECT event_id FROM core_events WHERE event_name = ? AND source_id = ?',
        (event_name, str(source_id)),
    ).fetchone()
    if existing:
        return str(existing['event_id'])
    if event_name == 'user.registered':
        if registered_user_id is None or any(value is not None for value in (order_id, trial, expired_key)):
            raise ValueError('registration event requires a user without a payment or key')
        payment = None
        user_id = registered_user_id
    elif registered_user_id is not None:
        raise ValueError('registered user is only valid for user.registered')
    elif event_name == 'key.expired':
        if expired_key is None or order_id is not None or trial is not None:
            raise ValueError('expiration event requires a key without a payment')
        payment = None
        user_id = expired_key['user_id']
    else:
        if expired_key is not None:
            raise ValueError('expired key is only valid for key.expired')
        payment = payment_with_conn(conn, order_id)
        if payment is None:
            raise ValueError('event payment does not exist')
        user_id = payment['user_id']
    user = conn.execute(
        'SELECT id AS user_id, telegram_id, referred_by AS referrer_user_id '
        'FROM users WHERE id = ?', (user_id,),
    ).fetchone()
    if user is None:
        raise ValueError('event user does not exist')
    user_snapshot = dict(user)
    user_snapshot['payments'] = summary_with_conn(conn, user_id)
    occurred_at = (
        conn.execute('SELECT created_at FROM users WHERE id = ?', (user_id,)).fetchone()[0]
        if event_name == 'user.registered'
        else conn.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0]
    )
    event_id = f'evt_{uuid4().hex}'
    payload = {
        'contract_version': 1, 'event_id': event_id, 'event': event_name,
        'occurred_at': iso_utc(occurred_at), 'user_id': user['user_id'],
        'telegram_id': user['telegram_id'], 'user': user_snapshot,
        'payment': payment,
    }
    if trial is not None:
        payload['trial'] = trial
    if expired_key is not None:
        payload['key'] = {
            'id': expired_key['id'],
            'custom_name': expired_key['custom_name'],
            'expires_at': iso_utc(expired_key['expires_at']),
            'tariff_id': expired_key['tariff_id'],
            'server_id': expired_key['server_id'],
        }
    conn.execute(
        'INSERT INTO core_events (event_id, event_name, source_id, occurred_at, payload) '
        'VALUES (?, ?, ?, ?, ?)',
        (event_id, event_name, str(source_id), occurred_at,
         json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))),
    )
    conn.executemany(
        'INSERT INTO extension_event_deliveries (event_id, extension_id, handler_name) '
        'VALUES (?, ?, ?)',
        [(event_id, item['extension_id'], item['handler_name']) for item in subscribers],
    )
    return event_id


_DUE = DELIVERY_DUE_SQL


def record_key_delivery_event(
    order_id: str, *, key_id: int, telegram_id: int, subscribers=(),
) -> str | None:
    """Record initial owner delivery once per purchase/trial order."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute(
            '''SELECT p.order_id FROM payments p
            JOIN users u ON u.id = p.user_id
            JOIN vpn_keys k ON k.id = p.vpn_key_id AND k.user_id = p.user_id
            WHERE p.order_id = ? AND p.status = 'paid'
                AND p.vpn_key_id = ? AND u.telegram_id = ?
                AND (p.purpose IN ('key_purchase', 'trial') OR p.payment_type = 'trial')''',
            (order_id, key_id, telegram_id),
        ).fetchone()
        if order is None:
            return None
        return record_core_event_with_conn(
            conn, event_name='key.delivered', source_id=order_id,
            order_id=order_id, subscribers=subscribers,
        )


def get_due_core_event_delivery_ids(
    limit: int = 100, *, event_id: str | None = None,
) -> list[int]:
    with get_db() as conn:
        event_filter = ' AND event_id = ?' if event_id is not None else ''
        params = [event_id] if event_id is not None else []
        params.append(max(1, min(int(limit), 100)))
        rows = conn.execute(
            f'SELECT id FROM extension_event_deliveries WHERE {_DUE}'
            f'{event_filter} ORDER BY id LIMIT ?',
            params,
        ).fetchall()
        return [int(row['id']) for row in rows]


def claim_core_event_delivery(delivery_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        job = claim_delivery_with_conn(conn, 'extension_event_deliveries', delivery_id)
        if job is None:
            return None
        row = conn.execute(
            'SELECT event_name, payload FROM core_events WHERE event_id = ?',
            (job['event_id'],),
        ).fetchone()
        # Parsing is part of the worker error boundary, not the atomic claim.
        return {**job, **dict(row)}


def finish_core_event_delivery(
    delivery_id: int, claim_token: str, *, state: str,
    error_code: str | None = None, retry_seconds: int = 0,
) -> bool:
    return finish_extension_delivery(
        'extension_event_deliveries', delivery_id, claim_token,
        state=state, error_code=error_code, retry_seconds=retry_seconds,
    )


def get_core_event_diagnostics() -> dict[str, Any]:
    return extension_delivery_diagnostics('extension_event_deliveries')
