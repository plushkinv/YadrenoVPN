"""Atomic, nonfinancial key grants using the existing extension operation journal."""
import hashlib
import json
import time
from contextlib import nullcontext

from core.results import CoreError
from .connection import get_db
from .db_extension_core import (
    build_extension_core_request_fingerprint, claim_extension_core_operation,
    finalize_extension_core_operation,
)

__all__ = ['get_extension_key_tariff', 'apply_extension_key_plan',
           'finish_extension_key_access', 'get_pending_extension_key_access']


def get_extension_key_tariff(selection, *, _conn=None):
    from .db_tariffs import _get_tariff_by_id_with_conn, _get_admin_custom_tariff_with_conn
    with (nullcontext(_conn) if _conn is not None else get_db()) as conn:
        if 'tariff_id' in selection:
            tariff = _get_tariff_by_id_with_conn(conn, selection['tariff_id'])
            if not tariff or tariff.get('system_type') is not None:
                raise CoreError('tariff_unavailable')
            return tariff
        terms = selection['custom_terms']
        tariff = _get_admin_custom_tariff_with_conn(conn, terms['group_id'])
        if not tariff:
            raise CoreError('tariff_unavailable')
        return {**tariff, **terms, 'price_minor': terms.get('price_minor', 0), 'extension_custom_terms': True}


def apply_extension_key_plan(*, extension_id, idempotency_key, user_id, operation, reason,
                             selection, key_id=None, server_id=None, source='system'):
    from .db_keys import _create_initial_vpn_key_with_conn
    from .db_key_plans import apply_key_plan_with_conn, set_custom_key_overrides_with_conn
    from .db_key_operations import _assert_key_mutation_ready
    from .db_modules import begin_module_operation
    from .db_business_operations import record_key_operation
    from .db_groups import get_active_servers_by_group

    payload = {**selection, 'key_id': key_id, 'server_id': server_id}
    fingerprint = build_extension_core_request_fingerprint(
        operation=operation, target_user_id=user_id, reason=reason, payload=payload)
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        receipt = claim_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key, operation=operation,
            target_user_id=user_id, amount=None, reason=reason,
            request_fingerprint=fingerprint, request_payload=payload, _conn=conn)
        if not receipt['claimed']:
            return receipt
        owner = conn.execute('SELECT is_banned FROM users WHERE id=?', (user_id,)).fetchone()
        if not owner or owner['is_banned']:
            raise CoreError('access_denied')
        tariff = get_extension_key_tariff(selection, _conn=conn)
        days = int(tariff.get('duration_days') or 0)
        traffic_limit = int(tariff.get('traffic_limit_gb') or 0) * 1024**3
        before = None
        if operation == 'renew_key':
            _assert_key_mutation_ready(conn, key_id)
            key = conn.execute("SELECT k.*,COALESCE(json_extract(e.tariff_json,'$.group_id'),t.group_id) AS group_id "
                               'FROM vpn_keys k LEFT JOIN tariffs t ON t.id=k.tariff_id '
                               'LEFT JOIN key_entitlements e ON e.key_id=k.id WHERE k.id=? AND k.user_id=?',
                               (key_id, user_id)).fetchone()
            if not key:
                raise CoreError('subscription_not_found')
            if key['group_id'] != tariff['group_id']:
                raise CoreError('tariff_unavailable')
            before = key['expires_at']
            apply_key_plan_with_conn(conn, dict(key), tariff, days, traffic_limit)
            # As with an explicit admin plan change, the new grant replaces the old entitlement.
            conn.execute('DELETE FROM key_entitlements WHERE key_id=?', (key_id,))
        elif operation == 'issue_key':
            servers = get_active_servers_by_group(tariff['group_id'])
            if server_id is None:
                if len(servers) != 1:
                    raise CoreError('server_selection_required' if servers else 'server_unavailable')
                server_id = servers[0]['id']
            if not any(server['id'] == server_id for server in servers):
                raise CoreError('server_unavailable')
            key_id = _create_initial_vpn_key_with_conn(
                conn, user_id, tariff['id'], days, traffic_limit)
            set_custom_key_overrides_with_conn(conn, key_id, tariff)
        else:
            raise ValueError('unsupported key grant operation')
        metadata = {'key_id': key_id, 'tariff': tariff, 'access_status': 'pending'}
        if operation == 'issue_key':
            inputs = {'key_id': key_id, 'server_id': server_id}
            configure_key = 'extension-grant:' + hashlib.sha256(
                (extension_id + ':' + idempotency_key).encode()).hexdigest()
            configure = begin_module_operation(
                user_id, 'key.configure', configure_key,
                hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
                {'source': source, 'inputs': inputs, 'headless': True}, _conn=conn)
            metadata.update(configure_key=configure_key, operation_id=configure['id'])
        after = conn.execute('SELECT expires_at FROM vpn_keys WHERE id=?', (key_id,)).fetchone()['expires_at']
        metadata['key_operation_id'] = record_key_operation(
            key_id=key_id, user_id=user_id, operation_type='grant' if operation == 'issue_key' else 'renew',
            delta_days=days, source='extension:' + extension_id, reason=reason,
            reference_type='extension_core', reference_id=idempotency_key,
            expires_before=before, expires_after=after, metadata={'tariff_id': tariff['id']}, _conn=conn)
        return finalize_extension_core_operation(
            extension_id=extension_id, idempotency_key=idempotency_key,
            status='applied', metadata=metadata, _conn=conn)


def finish_extension_key_access(extension_id, idempotency_key, *, status='ready'):
    with get_db() as conn:
        conn.execute("UPDATE extension_core_operations SET metadata=json_set(metadata,'$.access_status',?),"
                     "updated_at=CURRENT_TIMESTAMP WHERE extension_id=? AND idempotency_key=? AND status='applied'",
                     (status, extension_id, idempotency_key))


def get_pending_extension_key_access(limit=25, *, user_ids=None):
    from .db_extension_core import _row_result
    with get_db() as conn:
        owner_filter = '' if user_ids is None else (
            " AND EXISTS(SELECT 1 FROM vpn_keys k WHERE k.id=json_extract(metadata,'$.key_id') "
            'AND k.user_id IN (' + ','.join('?' for _ in user_ids) + '))')
        rows = conn.execute("SELECT * FROM extension_core_operations WHERE operation IN ('issue_key','renew_key') "
                            "AND status='applied' AND json_extract(metadata,'$.access_status')='pending'" + owner_filter +
                            " ORDER BY COALESCE(json_extract(metadata,'$.recovery_at'),0),created_at LIMIT ?",
                            (*(() if user_ids is None else user_ids), min(100, max(1, int(limit))))).fetchall()
        for row in rows:
            conn.execute("UPDATE extension_core_operations SET metadata=json_set(metadata,'$.recovery_at',?) "
                         'WHERE extension_id=? AND idempotency_key=?',
                         (time.time(), row['extension_id'], row['idempotency_key']))
        return [_row_result(row, already_applied=True) for row in rows]
