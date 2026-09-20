"""Immutable commercial terms for new orders and the access they grant."""
from __future__ import annotations

import json

from .connection import get_db

__all__ = ['get_payment_order_terms', 'get_key_entitlement', 'complete_key_entitlement_sync']
__all__ += ['save_payment_order_pricing', 'has_other_first_purchase_reservation', 'reserve_first_purchase_benefit']
__all__ += ['release_unbound_first_purchase_benefit']
__all__ += ['get_pending_trial_access']


def get_pending_trial_access(*, after_key_id=0, limit=25):
    """Only new trial entitlements need access recovery, never guessed old history."""
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            'SELECT ke.key_id,ke.order_id,k.user_id FROM key_entitlements ke '
            'JOIN vpn_keys k ON k.id=ke.key_id JOIN payments p ON p.order_id=ke.order_id '
            "WHERE p.purpose='trial' AND p.status='paid' AND ke.key_id>? "
            "AND NOT EXISTS(SELECT 1 FROM payment_effects e WHERE e.order_id=ke.order_id "
            "AND e.effect_name='panel_access' AND e.status='completed') ORDER BY ke.key_id LIMIT ?",
            (after_key_id, min(100, max(1, int(limit))))) ]


def get_payment_order_terms(order_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute('SELECT * FROM payment_order_terms WHERE order_id = ?', (order_id,)).fetchone()
        return {**dict(row), 'tariff': json.loads(row['tariff_json']),
                'pricing': json.loads(row['pricing_json']) if row['pricing_json'] else None} if row else None


def save_payment_order_pricing(order_id: str, pricing: dict) -> bool:
    """Freeze pricing before a provider is bound; old orders have no invented snapshot."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT pricing_json FROM payment_order_terms WHERE order_id=?', (order_id,)).fetchone()
        if not row:
            return True
        encoded = json.dumps(pricing, sort_keys=True, separators=(',', ':'))
        bound = conn.execute("SELECT 1 FROM payments p WHERE p.order_id=? AND (p.status<>'pending' OR "
                             'EXISTS(SELECT 1 FROM payment_provider_orders o WHERE o.order_id=p.order_id))',
                             (order_id,)).fetchone()
        if bound and row['pricing_json'] != encoded:
            return False
        return conn.execute('UPDATE payment_order_terms SET pricing_json=? WHERE order_id=?',
                            (encoded, order_id)).rowcount > 0


def release_unbound_first_purchase_benefit(order_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM account_operations WHERE kind='first_purchase_benefit' AND order_id=? "
                     "AND EXISTS(SELECT 1 FROM payments p WHERE p.order_id=? AND p.status='pending' "
                     'AND NOT EXISTS(SELECT 1 FROM payment_provider_orders o WHERE o.order_id=p.order_id))',
                     (order_id, order_id))


def has_other_first_purchase_reservation(user_id: int, order_id: str | None = None) -> bool:
    with get_db() as conn:
        return conn.execute('SELECT 1 FROM account_operations a JOIN payments p ON p.order_id=a.order_id '
                            "WHERE a.user_id=? AND a.kind='first_purchase_benefit' AND p.status<>'canceled' "
                            'AND a.order_id<>?', (user_id, order_id or '')).fetchone() is not None


def reserve_first_purchase_benefit(user_id: int, order_id: str) -> bool:
    """Reserve only a declared first-purchase benefit, never a price preview."""
    import secrets
    import time
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute('SELECT user_id,status FROM payments WHERE order_id=?', (order_id,)).fetchone()
        if not order or order['user_id'] != user_id or order['status'] != 'pending':
            return False
        conn.execute("DELETE FROM account_operations WHERE user_id=? AND kind='first_purchase_benefit' "
                     "AND order_id IN(SELECT order_id FROM payments WHERE status='canceled')", (user_id,))
        previous = conn.execute("SELECT order_id FROM account_operations WHERE user_id=? AND kind='first_purchase_benefit'",
                                (user_id,)).fetchone()
        if previous:
            return previous['order_id'] == order_id
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,order_id,created_at) '
                     "VALUES(?,?,'first_purchase_benefit','first_purchase',?,'{}',?,?)",
                     (secrets.token_urlsafe(24), user_id, order_id, order_id, int(time.time())))
        return True


def get_key_entitlement(key_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute('SELECT ke.*, EXISTS(SELECT 1 FROM payment_effects e WHERE e.order_id=ke.order_id '
                            "AND e.effect_name='panel_access' AND e.status='completed') AS panel_applied "
                            'FROM key_entitlements ke WHERE key_id=?', (key_id,)).fetchone()
        return {**dict(row), 'tariff': json.loads(row['tariff_json'])} if row else None


def complete_key_entitlement_sync(key_id: int, order_id: str) -> bool:
    """Record a confirmed panel effect only for the still-current purchased terms."""
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT k.id FROM key_entitlements ke JOIN vpn_keys k ON k.id=ke.key_id '
                            "WHERE ke.key_id=? AND ke.order_id=? AND k.server_id IS NOT NULL AND k.panel_email<>'' AND k.sub_id<>''",
                            (key_id, order_id)).fetchone()
        if not row:
            return False
        from .db_payment_intents import _complete_effect_in_connection
        _complete_effect_in_connection(conn, order_id, 'panel_access', {'key_id': key_id})
        return True


def _save_order_terms_with_conn(conn, order_id: str, tariff_id: int | None, currency: str) -> None:
    from core.context import get_account_context
    from .db_tariffs import _get_tariff_by_id_with_conn
    tariff = _get_tariff_by_id_with_conn(conn, tariff_id) if tariff_id else None
    account = get_account_context()
    snapshot = dict(tariff) if tariff else {}
    snapshot['base_currency'] = currency
    conn.execute('INSERT INTO payment_order_terms(order_id,version,source,tariff_json) VALUES (?, 1, ?, ?)',
                 (order_id, account.source if account else 'system', json.dumps(snapshot, sort_keys=True)))


def _save_entitlement_with_conn(conn, order_id: str, key_id: int) -> None:
    conn.execute('''INSERT INTO key_entitlements(key_id, order_id, tariff_json)
        SELECT ?, order_id, tariff_json FROM payment_order_terms WHERE order_id = ?
        ON CONFLICT(key_id) DO UPDATE SET order_id = excluded.order_id, tariff_json = excluded.tariff_json''',
        (key_id, order_id))
