"""Own-account nonfinancial grants and recovery of their panel access."""
import logging
from contextlib import nullcontext

from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.services.user_locks import user_locks
from core.accounts import owned_key, require_account
from core.context import AccountContext, bind_account_context
from core.extension_key_terms import integer, normalize_terms, require_lifecycle_lock_available
from core.results import CoreError
from database import requests as db

logger = logging.getLogger(__name__)


@regular_panel_operation
async def grant_key(account, extension_id, *, tariff_id=None, custom_terms=None, key_id=None,
                    server_id=None, reason, idempotency_key):
    from runtime.readiness import require_active
    require_active()
    require_account(account)
    require_lifecycle_lock_available(account)
    selection = normalize_terms(tariff_id, custom_terms)
    reason = db.normalize_extension_core_reason(reason)
    idempotency_key = db.normalize_extension_core_idempotency_key(idempotency_key)
    if key_id is not None:
        integer(key_id, 'key_id', minimum=1, maximum=2**63 - 1)
        if server_id is not None:
            raise CoreError('invalid_request', details={'field': 'server_id'})
    if server_id is not None:
        integer(server_id, 'server_id', minimum=1, maximum=2**63 - 1)
    operation = 'renew_key' if key_id is not None else 'issue_key'
    async with user_locks[account.account_id]:
        with bind_account_context(account):
            # A replay must work even after a catalog edit or while configure is pending.
            previous = db.get_extension_core_operation(extension_id=extension_id, idempotency_key=idempotency_key)
            if key_id is not None and previous is None:
                owned_key(account, key_id, mutation=True)
            receipt = db.apply_extension_key_plan(
                extension_id=extension_id, idempotency_key=idempotency_key, user_id=account.account_id,
                operation=operation, reason=reason, selection=selection,
                key_id=key_id, server_id=server_id, source=account.source)
            if receipt['status'] == 'idempotency_conflict':
                raise CoreError('idempotency_conflict')
            result = await resume_key_access(account, receipt, _locked=True)
    if receipt.get('applied'):
        from bot.services.key_lifecycle import emit_key_lifecycle_event_safe
        await emit_key_lifecycle_event_safe(
            'key_created' if operation == 'issue_key' else 'key_renewed',
            {'user_id': account.account_id, 'telegram_id': account.telegram_id,
             'key_id': result['key_id'], 'tariff_id': receipt['metadata']['tariff']['id'],
             'days': receipt['metadata']['tariff']['duration_days'],
             'reset_traffic': False, 'source': 'extension', 'result': result})
    return result


@regular_panel_operation
async def resume_key_access(account, receipt, *, _locked=False):
    """Retry absolute desired state only; never reapply the grant's delta."""
    metadata = receipt.get('metadata') or {}
    result = {k: receipt.get(k) for k in ('ok', 'status', 'applied', 'already_applied')}
    result.update(key_id=metadata.get('key_id'), operation_id=metadata.get('key_operation_id'),
                  db_applied=receipt.get('stored_status') == 'applied',
                  access_status=metadata.get('access_status'), error_code=None)
    if not result['db_applied']:
        result['error_code'] = receipt['status']
        return result
    async with (nullcontext() if _locked else user_locks[account.account_id]):
        with bind_account_context(account):
            key = db.get_vpn_key_by_id(metadata['key_id'])
            if not key or key['user_id'] != account.account_id:
                result.update(access_status='unavailable', error_code='subscription_not_found')
                db.finish_extension_key_access(receipt['extension_id'], receipt['idempotency_key'], status='unavailable')
                return result
            if metadata.get('access_status') == 'ready':
                entitlement = db.get_key_entitlement(key['id'])
                if (not all(key.get(field) for field in ('server_id', 'panel_email', 'sub_id'))
                        or (entitlement and not entitlement['panel_applied'])):
                    result.update(access_status='pending', error_code='panel_provisioning_pending')
                return result
            try:
                if metadata.get('configure_key'):
                    from core.key_operations import mutate_key
                    configure = db.get_module_operation(account.account_id, 'key.configure', metadata['configure_key'])
                    await mutate_key(account, 'configure', configure['request']['inputs'], metadata['configure_key'],
                                     _locked=True, _recovery=True)
                else:
                    owned_key(account, key['id'], mutation=True, _settlement=True)
                    if not all(key.get(field) for field in ('server_id', 'panel_email', 'sub_id')):
                        raise CoreError('key_configuration_pending', retryable=True)
                    from bot.services.vpn_api import sync_key_to_panel_state
                    synced = await sync_key_to_panel_state(key['id'])
                    if not synced.get('ok') or synced.get('errors'):
                        raise CoreError('panel_provisioning_pending', retryable=True)
                current = db.get_vpn_key_by_id(key['id'])
                if not current or not all(current.get(field) for field in ('server_id', 'panel_email', 'sub_id')):
                    raise CoreError('key_configuration_pending', retryable=True)
                db.finish_extension_key_access(receipt['extension_id'], receipt['idempotency_key'])
                result['access_status'] = 'ready'
            except Exception as error:
                logger.info('Extension key access pending key=%s error_type=%s', key['id'], type(error).__name__)
                result.update(access_status='pending', error_code='panel_provisioning_pending')
    return result


async def recover_extension_key_access(limit=25, *, user_ids=None):
    for receipt in db.get_pending_extension_key_access(limit, user_ids=user_ids):
        user = db.get_user_by_id(db.resolve_account_id(receipt['target_user_id']))
        if user:
            await resume_key_access(AccountContext(user['id'], user.get('telegram_id'), 'system'), receipt)
