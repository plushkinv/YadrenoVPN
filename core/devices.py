"""Own-account device operations with stable client identity on retries."""
from dataclasses import asdict, replace

from core.accounts import owned_key, require_account
from core.context import bind_account_context
from core.operations import operation_fingerprint, positive_id
from core.results import CoreError
from database import requests as db


async def list_devices(account, key_id):
    from bot.services.vpn_api import read_key_devices
    key = owned_key(account, positive_id(key_id, 'key_id'))
    try:
        return [asdict(device) for device in await read_key_devices(key)]
    except Exception:
        raise CoreError('devices_unavailable', retryable=True) from None


async def delete_device(account, key_id, device_id, idempotency_key, *, _recovery=False):
    from bot.services.panel_sync_coordinator import panel_sync_coordinator
    from bot.services.user_locks import user_locks
    from bot.services.vpn_api import read_key_devices, _delete_key_device
    from core.panel_identity import physical_panel_key
    require_account(account, _settlement=_recovery)
    positive_id(key_id, 'key_id')
    if (not isinstance(device_id, str) or not device_id.isascii() or not device_id.isdecimal()
            or len(device_id) > 20 or int(device_id) <= 0):
        raise CoreError('invalid_request', details={'field': 'device_id'})
    inputs = {'key_id': key_id, 'device_id': device_id}
    fingerprint = operation_fingerprint(idempotency_key, inputs)
    async with panel_sync_coordinator.regular(), user_locks[account.account_id]:
        operation = db.get_module_operation(account.account_id, 'key.device_delete', idempotency_key)
        if operation and operation['fingerprint'] != fingerprint:
            raise CoreError('idempotency_conflict')
        if _recovery and not operation:
            raise CoreError('operation_not_found')
        if operation and operation['result'] is not None:
            return {'operation_id': operation['id'], **operation['result']}
        actor = replace(account, operation_id=operation['id'] if operation else account.operation_id)
        with bind_account_context(actor):
            key = owned_key(actor, key_id, mutation=True, _settlement=_recovery)
            if not key.get('server_id') or not key.get('panel_email') or not key.get('sub_id'):
                raise CoreError('action_unavailable')
            binding = {'email': key['panel_email'], 'sub_id': key['sub_id'],
                       'endpoint': physical_panel_key(db.get_server_by_id(key['server_id']))}
            operation = operation or db.begin_module_operation(account.account_id, 'key.device_delete', idempotency_key,
                    fingerprint, {'source': account.source, 'inputs': inputs, 'binding': binding})
            if binding != operation['request']['binding']:
                result = {'deleted': False, 'reason': 'subscription_changed', 'key_id': key_id}
            else:
                try:
                    devices = await read_key_devices(key)
                    present = any(str(device.id) == device_id for device in devices)
                    if not present:
                        result = {'key_id': key_id, 'deleted': bool(operation['request'].get('progress', {}).get('observed'))}
                    else:
                        db.save_key_operation_progress(account.account_id, operation['id'], {'observed': True})
                        deleted = await _delete_key_device(key, device_id)
                        if not deleted:
                            raise CoreError('device_operation_pending', retryable=True)
                        result = {'key_id': key_id, 'deleted': True}
                except Exception:
                    raise CoreError('device_operation_pending', retryable=True, operation_id=operation['id']) from None
            db.finish_module_operation(account.account_id, operation['id'], result)
            return {'operation_id': operation['id'], **result}
