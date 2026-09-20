"""Atomic receipts around existing non-financial account commands."""
import json
import secrets
import time

from core.results import CoreError
from .connection import get_db

__all__ = ['apply_account_promotion_once']


def apply_account_promotion_once(user_id, action, code, idempotency_key, fingerprint, source):
    from .db_promotions import activate_user_promo_code, clear_user_active_promo_code
    kind = 'promotion.' + action
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT * FROM account_operations WHERE user_id=? AND kind=? AND idempotency_key=?',
                                (user_id, kind, idempotency_key)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            return {'operation_id': previous['id'], **json.loads(previous['result_json'])}
        owner = conn.execute('SELECT is_banned FROM users WHERE id=?', (user_id,)).fetchone()
        if not owner or owner['is_banned']:
            raise CoreError('access_denied')
        if action == 'activate':
            result = activate_user_promo_code(user_id, code, _conn=conn)
        elif action == 'clear':
            result = {'ok': clear_user_active_promo_code(user_id, _conn=conn)}
        else:
            raise ValueError('unsupported promotion action')
        operation_id = secrets.token_urlsafe(24)
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,result_json,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?)', (operation_id, user_id, kind, idempotency_key, fingerprint,
                     json.dumps({'source': source, 'inputs': {'code': code}}), json.dumps(result), int(time.time())))
        return {'operation_id': operation_id, **result}
