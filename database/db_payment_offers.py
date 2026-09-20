"""Durable HTTP receipts pointing at the existing payment-intent ledger."""
from __future__ import annotations

import json
import secrets
import time

from core.results import CoreError
from .connection import get_db

__all__ = ['save_payment_offer', 'get_payment_offer', 'begin_offered_order',
           'get_account_payment_operation', 'finish_account_payment_operation',
           'remember_payment_operation_alias', 'expire_unprepared_payment_offers']


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    for field in ('payload', 'quote', 'request', 'result'):
        raw = result.pop(field + '_json', None)
        if raw is not None:
            result[field] = json.loads(raw)
    return result


def save_payment_offer(user_id: int, payload: dict, quote: dict, *, seconds: int = 300) -> dict:
    now, offer_id = int(time.time()), secrets.token_urlsafe(24)
    with get_db() as conn:
        conn.execute('INSERT INTO payment_offers(id,user_id,payload_json,quote_json,created_at,expires_at) '
                     'VALUES(?,?,?,?,?,?)',
                     (offer_id, user_id, _json(payload), _json(quote), now, now + seconds))
    return get_payment_offer(offer_id, user_id)


def get_payment_offer(offer_id: str, user_id: int) -> dict | None:
    with get_db() as conn:
        return _decode(conn.execute('SELECT * FROM payment_offers WHERE id=? AND user_id=?',
                                    (offer_id, user_id)).fetchone())


def get_account_payment_operation(user_id: int, *, idempotency_key: str | None = None,
                                  order_id: str | None = None) -> dict | None:
    if (idempotency_key is None) == (order_id is None):
        raise ValueError('one operation selector is required')
    with get_db() as conn:
        selector, value = ('idempotency_key', idempotency_key) if idempotency_key is not None else ('order_id', order_id)
        return _decode(conn.execute(f"SELECT * FROM account_operations WHERE user_id=? AND kind='payment' AND {selector}=? ORDER BY created_at,rowid LIMIT 1",
                                   (user_id, value)).fetchone())


def remember_payment_operation_alias(user_id: int, order_id: str, quote_id: str, idempotency_key: str) -> None:
    """A second device may reuse an offer, but its idempotency key stays bound too."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute("SELECT fingerprint FROM account_operations WHERE user_id=? AND kind='payment' AND idempotency_key=?",
                                (user_id, idempotency_key)).fetchone()
        if previous:
            if previous['fingerprint'] != quote_id:
                raise CoreError('idempotency_conflict')
            return
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,result_json,order_id,created_at) '
                     'SELECT ?,user_id,kind,?,fingerprint,request_json,result_json,order_id,? FROM account_operations '
                     "WHERE user_id=? AND kind='payment' AND order_id=? AND fingerprint=? ORDER BY created_at,rowid LIMIT 1",
                     (secrets.token_urlsafe(24), idempotency_key, int(time.time()), user_id, order_id, quote_id))


def expire_unprepared_payment_offers(*, limit: int = 100) -> int:
    """Release an interrupted pre-invoice preparation after its offer expires."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = conn.execute('SELECT DISTINCT p.order_id FROM payments p JOIN payment_offers q ON q.order_id=p.order_id '
                            'JOIN account_operations a ON a.order_id=p.order_id '
                            "WHERE p.status='pending' AND p.provider_confirmed_at IS NULL AND q.expires_at<=? "
                            'AND a.result_json IS NULL AND NOT EXISTS(SELECT 1 FROM payment_provider_orders o WHERE o.order_id=p.order_id) '
                            'LIMIT ?', (int(time.time()), min(100, max(1, int(limit))))).fetchall()
        for row in rows:
            conn.execute("UPDATE payments SET status='canceled' WHERE order_id=?", (row['order_id'],))
            conn.execute("UPDATE promo_redemptions SET status='canceled' WHERE order_id=? AND status='reserved'", (row['order_id'],))
            conn.execute("UPDATE account_operations SET result_json=? WHERE order_id=? AND kind='payment'",
                         (_json({'error': 'quote_expired'}), row['order_id']))
        return len(rows)


def begin_offered_order(user_id: int, offer_id: str, idempotency_key: str, *, description: str,
                        success_target: dict, cancel_target: dict) -> dict:
    """Claim one offer and allocate its single recoverable intent atomically."""
    from .db_payment_intents import _create_payment_intent_record_with_conn
    from .db_payments import _tariff_payment_target_allowed
    from .db_tariffs import _get_tariff_by_id_with_conn
    now = int(time.time())
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute("SELECT * FROM account_operations WHERE user_id=? AND kind='payment' AND idempotency_key=?",
                                (user_id, idempotency_key)).fetchone()
        if previous:
            if previous['fingerprint'] != offer_id:
                raise CoreError('idempotency_conflict')
            return _decode(previous)
        offer = _decode(conn.execute('SELECT * FROM payment_offers WHERE id=? AND user_id=?',
                                     (offer_id, user_id)).fetchone())
        if not offer:
            raise CoreError('quote_not_found')
        # A second idempotency key cannot turn the same confirmed offer into a second order.
        if offer['order_id']:
            return _decode(conn.execute("SELECT * FROM account_operations WHERE user_id=? AND kind='payment' AND order_id=?",
                                        (user_id, offer['order_id'])).fetchone())
        if offer['expires_at'] <= now:
            raise CoreError('quote_expired')
        owner = conn.execute('SELECT is_banned FROM users WHERE id=?', (user_id,)).fetchone()
        if not owner or owner['is_banned']:
            raise CoreError('access_denied')
        payload, quote = offer['payload'], offer['quote']
        tariff_id, key_id = payload.get('tariff_id'), payload.get('key_id')
        if tariff_id:
            current = _get_tariff_by_id_with_conn(conn, tariff_id)
            if (not current or dict(current) != quote['tariff'] or not _tariff_payment_target_allowed(
                    conn, user_id=user_id, tariff_id=tariff_id, vpn_key_id=key_id)):
                raise CoreError('quote_changed')
        purpose_data = {key: payload[key] for key in ('tariff_id', 'key_id') if payload.get(key) is not None}
        _, order_id = _create_payment_intent_record_with_conn(
            conn, user_id=user_id, purpose=payload['purpose'], purpose_data=purpose_data,
            nominal_amount_minor=quote['pricing']['nominal_amount_minor'],
            base_currency=quote['pricing']['base_currency'], description=description,
            success_target=success_target, cancel_target=cancel_target, tariff_id=tariff_id,
            vpn_key_id=key_id, period_days=quote['tariff'].get('duration_days'),
        )
        operation_id = secrets.token_urlsafe(24)
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,order_id,created_at) '
                     "VALUES(?,?,'payment',?,?,?,?,?)",
                     (operation_id, user_id, idempotency_key, offer_id, _json({'quote_id': offer_id}), order_id, now))
        conn.execute('UPDATE payment_offers SET order_id=? WHERE id=?', (order_id, offer_id))
        return _decode(conn.execute('SELECT * FROM account_operations WHERE id=?', (operation_id,)).fetchone())


def finish_account_payment_operation(user_id: int, order_id: str, result: dict) -> bool:
    with get_db() as conn:
        return conn.execute("UPDATE account_operations SET result_json=? WHERE user_id=? AND kind='payment' "
                            'AND order_id=? AND result_json IS NULL', (_json(result), user_id, order_id)).rowcount > 0
