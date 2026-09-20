"""Persist shared declarations and own-account module operation receipts."""
import json
import secrets
import time

from core.results import CoreError
from .connection import get_db

__all__ = ['get_core_module_manifests', 'save_core_module_manifest',
           'get_module_operation', 'begin_module_operation', 'finish_module_operation']
__all__ += ['get_payment_module_reward', 'save_payment_module_reward']


def get_payment_module_reward(order_id, effect):
    with get_db() as conn:
        row = conn.execute("SELECT metadata_json FROM payment_effects WHERE order_id=? AND effect_name=? AND status='completed'",
                            (order_id, effect)).fetchone()
        return json.loads(row['metadata_json']) if row else None


def save_payment_module_reward(order_id, effect, reward):
    from .db_payment_intents import _complete_effect_in_connection
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT metadata_json FROM payment_effects WHERE order_id=? AND effect_name=? AND status='completed'",
                            (order_id, effect)).fetchone()
        if row:
            return json.loads(row['metadata_json'])
        _complete_effect_in_connection(conn, order_id, effect, reward)
        return reward


def get_core_module_manifests():
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return {}
        return {row['key'].removeprefix('core_module_manifest.'): json.loads(row['value'])
                for row in conn.execute("SELECT key,value FROM settings WHERE key LIKE 'core_module_manifest.%'")}


def save_core_module_manifest(module_id, manifest):
    from .db_settings import set_setting
    set_setting('core_module_manifest.' + module_id, json.dumps(manifest, sort_keys=True, allow_nan=False))


def _operation(row):
    if row is None:
        return None
    value = dict(row)
    value['request'] = json.loads(value.pop('request_json'))
    raw = value.pop('result_json')
    value['completed'] = raw is not None
    value['result'] = json.loads(raw) if raw is not None else None
    return value


def get_module_operation(user_id, kind, idempotency_key):
    with get_db() as conn:
        return _operation(conn.execute('SELECT * FROM account_operations WHERE user_id=? AND kind=? AND idempotency_key=?',
                                       (user_id, kind, idempotency_key)).fetchone())


def begin_module_operation(user_id, kind, idempotency_key, fingerprint, request):
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT * FROM account_operations WHERE user_id=? AND kind=? AND idempotency_key=?',
                                (user_id, kind, idempotency_key)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            return _operation(previous)
        owner = conn.execute('SELECT is_banned FROM users WHERE id=?', (user_id,)).fetchone()
        if not owner or owner['is_banned']:
            raise CoreError('access_denied')
        operation_id = secrets.token_urlsafe(24)
        conn.execute('INSERT INTO account_operations(id,user_id,kind,idempotency_key,fingerprint,request_json,created_at) '
                     'VALUES(?,?,?,?,?,?,?)', (operation_id, user_id, kind, idempotency_key, fingerprint,
                                              json.dumps(request, allow_nan=False, sort_keys=True), int(time.time())))
        return _operation(conn.execute('SELECT * FROM account_operations WHERE id=?', (operation_id,)).fetchone())


def finish_module_operation(user_id, operation_id, result):
    with get_db() as conn:
        conn.execute('UPDATE account_operations SET result_json=? WHERE id=? AND user_id=? AND result_json IS NULL',
                     (json.dumps(result, allow_nan=False, sort_keys=True), operation_id, user_id))
