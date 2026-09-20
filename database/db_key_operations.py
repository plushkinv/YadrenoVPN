"""Progress for existing key actions, using the shared account operation journal."""
import json
import time

from core.context import get_account_context
from core.results import CoreError
from .connection import get_db

__all__ = ['get_account_key_operation', 'save_key_operation_progress', 'switch_key_operation_binding',
           'rename_account_key', 'get_pending_account_key_operations', 'assert_key_mutation_ready',
           'get_pending_key_mutation_ids', 'mark_key_operation_attempt']


def _pending_key_mutation_ids(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ids = set()
    if 'panel_identity_renames' in tables:
        ids.update(row[0] for row in conn.execute("SELECT key_id FROM panel_identity_renames WHERE state<>'done'"))
    if 'account_operations' in tables:
        ids.update(row[0] for row in conn.execute("SELECT json_extract(request_json,'$.inputs.key_id') FROM account_operations "
                    "WHERE kind IN ('key.replace','key.configure','key.delete','key.device_delete') AND result_json IS NULL"))
    return frozenset(ids - {None})


def get_pending_key_mutation_ids():
    with get_db() as conn:
        return _pending_key_mutation_ids(conn)


def _assert_key_mutation_ready(conn, key_id):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('panel_identity_renames','account_operations')")}
    if 'panel_identity_renames' in tables and conn.execute(
            "SELECT 1 FROM panel_identity_renames WHERE key_id=? AND state<>'done'", (key_id,)).fetchone():
        raise CoreError('panel_identity_pending', retryable=True)
    if 'account_operations' in tables:
        actor = get_account_context()
        active = conn.execute("SELECT id FROM account_operations WHERE kind IN ('key.replace','key.configure','key.delete','key.device_delete') AND result_json IS NULL "
                              "AND json_extract(request_json,'$.inputs.key_id')=? AND id<>? LIMIT 1",
                              (key_id, (actor.operation_id or '') if actor else '')).fetchone()
        if active:
            raise CoreError('key_operation_pending', retryable=True, operation_id=active['id'])


def assert_key_mutation_ready(key_id):
    with get_db() as conn:
        _assert_key_mutation_ready(conn, key_id)


def get_account_key_operation(user_id, operation_id):
    from .db_modules import _operation
    with get_db() as conn:
        return _operation(conn.execute("SELECT * FROM account_operations WHERE id=? AND user_id=? AND kind LIKE 'key.%'",
                                       (operation_id, user_id)).fetchone())


def save_key_operation_progress(user_id, operation_id, progress):
    with get_db() as conn:
        conn.execute("UPDATE account_operations SET request_json=json_set(request_json,'$.progress',json(?)) "
                     "WHERE user_id=? AND id=? AND kind LIKE 'key.%' AND result_json IS NULL",
                     (json.dumps(progress, sort_keys=True), user_id, operation_id))


def switch_key_operation_binding(user_id, operation_id, *, imported_snapshot=None):
    """Compare-and-swap the original binding and advance progress in one transaction."""
    from .db_modules import _operation
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        operation = _operation(conn.execute("SELECT * FROM account_operations WHERE id=? AND user_id=? AND kind IN ('key.replace','key.configure')",
                                            (operation_id, user_id)).fetchone())
        if not operation:
            raise CoreError('operation_not_found')
        request = operation['request']
        key_id, progress = request['inputs']['key_id'], request['progress']
        _assert_key_mutation_ready(conn, key_id)
        old, target = progress['old'], progress['target']
        changed = conn.execute('UPDATE vpn_keys SET server_id=?,panel_email=?,sub_id=?,traffic_used=?,traffic_limit=?,expires_at=?, '
                               'traffic_limit_override=CASE WHEN tariff_id IS NULL THEN ? ELSE traffic_limit_override END '
                               'WHERE id=? AND user_id=? AND server_id IS ? AND panel_email IS ? AND sub_id IS ?',
                               (target['server_id'], target['email'], target['sub_id'], progress['traffic_used'],
                                progress['traffic_limit'], progress['expires_at'], progress['traffic_limit'],
                                key_id, user_id, old['server_id'], old['email'], old['sub_id'])).rowcount
        if not changed:
            raise CoreError('panel_identity_changed')
        if imported_snapshot is not None:
            member = conn.execute('SELECT snapshot_json FROM subscription_import_members WHERE key_id=?', (key_id,)).fetchone()
            if member:
                snapshot = json.loads(member['snapshot_json'])
                snapshot['replacement'] = imported_snapshot
                conn.execute('UPDATE subscription_import_members SET email=?,snapshot_json=? WHERE key_id=?',
                             (target['email'], json.dumps(snapshot, sort_keys=True), key_id))
        progress['phase'] = 'switched'
        conn.execute('UPDATE account_operations SET request_json=? WHERE id=?', (json.dumps(request, sort_keys=True), operation_id))


def rename_account_key(user_id, key_id, name):
    with get_db() as conn:
        _assert_key_mutation_ready(conn, key_id)
        return conn.execute('UPDATE vpn_keys SET custom_name=? WHERE id=? AND user_id=?', (name, key_id, user_id)).rowcount > 0


def get_pending_account_key_operations(limit=25):
    from .db_modules import _operation
    with get_db() as conn:
        return [_operation(row) for row in conn.execute("SELECT * FROM account_operations WHERE kind IN ('key.replace','key.configure','key.delete','key.device_delete') "
                "AND result_json IS NULL ORDER BY COALESCE(json_extract(request_json,'$.recovery_at'),0),created_at,id LIMIT ?",
                (min(100, max(1, int(limit))),))]


def mark_key_operation_attempt(operation_id):
    with get_db() as conn:
        conn.execute("UPDATE account_operations SET request_json=json_set(request_json,'$.recovery_at',?) "
                     "WHERE id=? AND kind LIKE 'key.%' AND result_json IS NULL", (time.time(), operation_id))
