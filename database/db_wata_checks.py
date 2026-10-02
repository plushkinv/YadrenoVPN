"""Atomic WATA check metadata and retained-invoice navigation."""
from __future__ import annotations

import json
from typing import Any

from .connection import get_db
from .db_payment_providers import _mark_provider_confirmed, _row_to_dict


def _read(conn, order_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM payment_provider_orders WHERE order_id = ? AND provider_id = 'wata'",
        (order_id,),
    ).fetchone()
    result = _row_to_dict(row)
    if result is not None and not isinstance(result['metadata'], dict):
        result['metadata'] = {}
    return result


def _write(conn, order: dict) -> None:
    conn.execute(
        """UPDATE payment_provider_orders SET metadata_json = ?, status = ?,
           updated_at = CURRENT_TIMESTAMP WHERE order_id = ? AND provider_id = 'wata'""",
        (json.dumps(order['metadata'], ensure_ascii=False), order['status'], order['order_id']),
    )


def claim_wata_payment_check(order_id: str, *, now: float, interval: int) -> tuple[dict, bool]:
    """Persist the request gate before HTTP, including across process restarts."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _read(conn, order_id)
        if order is None:
            raise ValueError('WATA invoice is missing')
        check = dict(order['metadata'].get('wata_check') or {})
        if order['status'] != 'pending' or float(check.get('not_before') or 0) > now:
            return order, False
        check['not_before'] = now + interval
        order['metadata']['wata_check'] = check
        _write(conn, order)
        return order, True


def save_wata_payment_check(order_id: str, **changes: Any) -> dict:
    """Merge check progress without losing navigation or downgrading settlement."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _read(conn, order_id)
        if order is None:
            raise ValueError('WATA invoice is missing')
        if order['status'] != 'pending':
            return order
        check = dict(order['metadata'].get('wata_check') or {})
        if 'not_before' in changes:
            changes['not_before'] = max(float(check.get('not_before') or 0), changes['not_before'])
        check.update(changes)
        order['metadata']['wata_check'] = check
        if check.get('transaction_id'):
            order['status'] = 'succeeded'
            _mark_provider_confirmed(conn, order_id)
        _write(conn, order)
        return order


def claim_wata_payment_replacement(
    order_id: str, *, user_id: int, replacement_id: str | None = None,
) -> str | None:
    """Reuse only an owned pending draft without a provider binding."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = _read(conn, order_id)
        source = conn.execute(
            """SELECT 1 FROM payments WHERE order_id = ? AND user_id = ?
               AND intent_version = 1 AND status = 'pending' AND provider_confirmed_at IS NULL""",
            (order_id, user_id),
        ).fetchone()
        if not source or not order or order['status'] != 'pending':
            return None
        if not (order['provider_payment_id'] or order['payment_url']):
            return None

        def available(candidate):
            return candidate and conn.execute(
                """SELECT 1 FROM payments p WHERE p.order_id = ? AND p.user_id = ?
                   AND p.intent_version = 1 AND p.status = 'pending' AND p.provider_confirmed_at IS NULL
                   AND NOT EXISTS (SELECT 1 FROM payment_provider_orders po WHERE po.order_id = p.order_id)""",
                (candidate, user_id),
            ).fetchone()

        previous = order['metadata'].get('replacement_order_id')
        if available(previous):
            return str(previous)
        if not available(replacement_id):
            return None
        order['metadata']['replacement_order_id'] = replacement_id
        _write(conn, order)
        return replacement_id


__all__ = ['claim_wata_payment_check', 'save_wata_payment_check', 'claim_wata_payment_replacement']
