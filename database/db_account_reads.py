"""Bounded own-account projections without provider or panel secrets."""
from .connection import get_db

__all__ = ['get_account_subscription_ids', 'get_account_payment_history', 'get_account_balance_history',
           'get_account_pending_key_operations', 'get_account_key_history']


def get_account_subscription_ids(user_id, *, limit=50, offset=0):
    with get_db() as conn:
        return [row[0] for row in conn.execute('SELECT id FROM vpn_keys WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
                                               (user_id, limit, offset))]


def get_account_payment_history(user_id, *, limit=50, offset=0):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT order_id,purpose,status,payment_type,base_currency,'
            'nominal_amount_minor,payable_amount_minor,balance_deduct_minor,vpn_key_id,period_days,created_at,paid_at '
            'FROM payments WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (user_id, limit, offset))]


def get_account_balance_history(user_id, *, limit=50, offset=0):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT id,operation_type,delta_minor,currency,balance_before,balance_after,'
            'reason,created_at FROM balance_operations WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
            (user_id, limit, offset))]


def get_account_pending_key_operations(user_id, key_id):
    with get_db() as conn:
        return [dict(row) for row in conn.execute('SELECT id,kind,created_at FROM account_operations WHERE user_id=? '
            "AND kind LIKE 'key.%' AND result_json IS NULL AND json_extract(request_json,'$.inputs.key_id')=?",
            (user_id, key_id))]


def get_account_key_history(user_id, key_id, *, limit=50, offset=0):
    with get_db() as conn:
        return [dict(row) for row in conn.execute("""
            SELECT order_id AS id,'payment' AS kind,purpose AS action,payment_type AS source,
                   status,payable_amount_minor,base_currency AS currency,period_days AS delta_days,
                   NULL AS expires_before,NULL AS expires_after,created_at
            FROM payments WHERE user_id=? AND vpn_key_id=?
            UNION ALL
            SELECT CAST(id AS TEXT),'key_operation',operation_type,source,'completed',NULL,NULL,
                   delta_days,expires_before,expires_after,created_at
            FROM key_operation_log WHERE user_id=? AND vpn_key_id=?
            ORDER BY created_at DESC,kind,id DESC LIMIT ? OFFSET ?
        """, (user_id, key_id, user_id, key_id, limit, offset))]
