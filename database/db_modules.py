"""Persist shared declarations and own-account module operation receipts."""
import json
import secrets
import time
from contextlib import nullcontext

from core.results import CoreError
from .connection import get_db

__all__ = ['get_core_module_manifests', 'save_core_module_manifest',
           'get_module_operation', 'begin_module_operation', 'finish_module_operation']
__all__ += ['get_payment_module_reward', 'save_payment_module_reward']
__all__ += ['get_module_operation_status', 'record_module_operation_error', 'cleanup_module_operations']


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
    value['completed'] = value.get('completed_at') is not None or raw is not None
    value['result'] = json.loads(raw) if raw is not None else None
    return value


def get_module_operation(user_id, kind, idempotency_key):
    with get_db() as conn:
        return _operation(conn.execute('SELECT * FROM account_operations WHERE user_id=? AND kind=? AND idempotency_key=?',
                                       (user_id, kind, idempotency_key)).fetchone())


def begin_module_operation(user_id, kind, idempotency_key, fingerprint, request, *, _conn=None):
    with (nullcontext(_conn) if _conn is not None else get_db()) as conn:
        if _conn is None:
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
        conn.execute('UPDATE account_operations SET result_json=?,completed_at=?,last_error_code=NULL '
                     'WHERE id=? AND user_id=? AND completed_at IS NULL AND result_json IS NULL',
                     (json.dumps(result, allow_nan=False, sort_keys=True), int(time.time()), operation_id, user_id))


def get_module_operation_status(user_id, module_id, operation_id):
    with get_db() as conn:
        row = conn.execute('SELECT * FROM account_operations WHERE id=? AND user_id=?',
                           (operation_id, user_id)).fetchone()
        return _operation(row) if row and row['kind'].startswith('module.' + module_id + '.') else None


def record_module_operation_error(user_id, operation_id, code):
    with get_db() as conn:
        conn.execute('UPDATE account_operations SET last_error_code=? WHERE id=? AND user_id=? '
                     'AND completed_at IS NULL', (code, operation_id, user_id))


def cleanup_module_operations(reads=(), *, now=None, limit=500):
    """Delete attested reads and expired completed module receipts in one batch."""
    now = int(time.time()) if now is None else now
    limit = max(1, min(500, limit))
    removed = 0
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for module_id, name, version in reads:
            ids = [row['id'] for row in conn.execute(
                "SELECT id FROM account_operations WHERE kind=? AND purged_at IS NULL "
                "AND completed_at IS NOT NULL "
                "AND json_valid(request_json) AND json_extract(request_json,'$.version')=? LIMIT ?",
                ('module.' + module_id + '.' + name, version, limit - removed))]
            conn.executemany('DELETE FROM account_operations WHERE id=?', ((value,) for value in ids))
            removed += len(ids)
            if removed >= limit:
                break
        ids = [row['id'] for row in conn.execute(
            "SELECT id FROM account_operations WHERE kind GLOB 'module.*' AND completed_at<=? "
            "ORDER BY completed_at,id LIMIT ?", (now - 30 * 86400, limit - removed))]
        conn.executemany('DELETE FROM account_operations WHERE id=?', ((value,) for value in ids))
    return removed + len(ids)
