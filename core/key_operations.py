"""Owner-checked shared key actions and durable retry identities."""
import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import replace

from bot.services.panel_sync_coordinator import regular_panel_operation
from bot.services.user_locks import user_locks
from core.accounts import owned_key, require_account
from core.context import AccountContext, bind_account_context
from core.results import CoreError
from database import requests as db

_ACTIONS = {'replace': 'key.replace.start', 'configure': 'key.configure.start',
            'rename': 'key.rename.start', 'delete': 'key.delete'}


@regular_panel_operation
async def mutate_key(account, action, inputs, idempotency_key, *, _policy_checked=False, _locked=False, _recovery=False):
    from runtime.readiness import require_active
    require_active()
    require_account(account, _settlement=_recovery)
    expected = {'key_id'} | ({'server_id'} if action in {'replace', 'configure'} else {'name'} if action == 'rename' else set())
    if action not in _ACTIONS or not isinstance(inputs, dict) or set(inputs) != expected:
        raise CoreError('invalid_request')
    for field in ('key_id', 'server_id'):
        if field in inputs and (type(inputs[field]) is not int or inputs[field] <= 0):
            raise CoreError('invalid_request', details={'field': field})
    if action == 'rename' and (not isinstance(inputs['name'], str) or not 1 <= len(inputs['name'].strip()) <= 30):
        raise CoreError('invalid_request', details={'field': 'name'})
    if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency_key):
        raise CoreError('invalid_request', details={'field': 'idempotency_key'})
    kind = 'key.' + action
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    async with (nullcontext() if _locked else user_locks[account.account_id]):
        previous = db.get_module_operation(account.account_id, kind, idempotency_key)
        if _recovery and not previous:
            raise CoreError('operation_not_found')
        if previous and previous['fingerprint'] != fingerprint:
            raise CoreError('idempotency_conflict')
        if previous and previous['result'] is not None:
            if previous['result'].get('error'):
                raise CoreError(previous['result']['error'], operation_id=previous['id'])
            return {'operation_id': previous['id'], **previous['result']}
        actor = replace(account, operation_id=previous['id'] if previous else account.operation_id,
                        source=previous['request']['source'] if previous else account.source)
        with bind_account_context(actor):
            key = db.get_vpn_key_by_id(inputs['key_id'])
            deleted_retry = action == 'delete' and previous and previous['request'].get('progress') and key is None
            if not deleted_retry:
                key = owned_key(actor, inputs['key_id'], mutation=True, _settlement=_recovery)
            if not previous and not _policy_checked:
                from bot.utils.action_policy import run_account_action_policies
                decision = await run_account_action_policies(_ACTIONS[action], {'key_id': inputs['key_id']}, account=actor, phase='execute')
                if decision['decision'] != 'continue':
                    raise CoreError('action_unavailable')
            if action == 'delete' and key and db.is_key_active(key):
                raise CoreError('action_unavailable')
            if not previous and action in ('replace', 'configure') and bool(key.get('server_id')) != (action == 'replace'):
                raise CoreError('action_unavailable')
            operation = previous or db.begin_module_operation(account.account_id, kind, idempotency_key, fingerprint,
                {'source': account.source, 'inputs': inputs})
            actor = replace(actor, operation_id=operation['id'])
            with bind_account_context(actor):
                try:
                    if action == 'replace':
                        from bot.services.key_replacement import replace_key
                        result = await replace_key(actor, operation, key)
                    elif action == 'configure':
                        order = db.find_latest_paid_order_for_key(inputs['key_id'])
                        if order:
                            from bot.services.new_key_setup import resolve_new_key_setup, _provision_resolved_new_key, NewKeySetupStatus
                            setup = await resolve_new_key_setup(order['order_id'], expected_telegram_id=actor.telegram_id,
                                                                server_id=inputs['server_id'])
                            if setup.status is NewKeySetupStatus.PROVISIONING:
                                setup = await _provision_resolved_new_key(setup)
                            if setup.status is not NewKeySetupStatus.READY:
                                raise CoreError(setup.error_code or 'key_configuration_pending', retryable=setup.retryable)
                            entitlement = db.get_key_entitlement(inputs['key_id'])
                            if entitlement and not entitlement['panel_applied']:
                                from bot.services.vpn_api import sync_key_to_panel_state
                                synced = await sync_key_to_panel_state(inputs['key_id'])
                                if not synced.get('ok') or synced.get('errors'):
                                    raise CoreError('panel_provisioning_pending', retryable=True)
                                if not db.complete_key_entitlement_sync(inputs['key_id'], entitlement['order_id']):
                                    raise CoreError('panel_provisioning_pending', retryable=True)
                            result = {'key_id': inputs['key_id'], 'state': 'completed'}
                        else:
                            from bot.services.key_replacement import replace_key
                            result = await replace_key(actor, operation, key)
                    elif action == 'delete':
                        from bot.services.key_deletion import delete_key
                        result = await delete_key(actor, operation, key)
                    elif action == 'rename':
                        if not db.rename_account_key(actor.account_id, inputs['key_id'], inputs['name'].strip()):
                            raise CoreError('subscription_not_found')
                        result = {'key_id': inputs['key_id'], 'state': 'completed'}
                    else:
                        raise CoreError('action_unavailable')
                    db.finish_module_operation(actor.account_id, operation['id'], result)
                    return {'operation_id': operation['id'], **result}
                except CoreError as exc:
                    current = db.get_module_operation(account.account_id, kind, idempotency_key)
                    progress = (current or {}).get('request', {}).get('progress') or {}
                    if progress.get('candidate_started') or progress.get('phase') == 'switched':
                        aborted = False
                        if not exc.retryable and progress.get('phase') == 'prepared':
                            from bot.services.key_replacement import abort_unswitched_candidate
                            try:
                                aborted = await abort_unswitched_candidate(current)
                            except Exception:
                                pass
                        if not aborted:
                            raise CoreError(exc.code, retryable=True, operation_id=operation['id']) from None
                    if not exc.retryable:
                        db.finish_module_operation(actor.account_id, operation['id'], {'state': 'failed', 'error': exc.code})
                        raise
                    raise CoreError(exc.code, retryable=True, operation_id=operation['id']) from None
                except Exception:
                    raise CoreError('key_operation_pending', retryable=True, operation_id=operation['id']) from None


async def recover_key_operations(limit=25):
    outcomes = {'completed': 0, 'pending': 0}
    for operation in db.get_pending_account_key_operations(limit):
        db.mark_key_operation_attempt(operation['id'])
        user = db.get_user_by_id(operation['user_id'])
        if not user:
            continue
        actor = AccountContext(user['id'], user.get('telegram_id'), 'system', operation['id'])
        try:
            if operation['kind'] == 'key.device_delete':
                from core.devices import delete_device
                await delete_device(actor, **operation['request']['inputs'],
                                    idempotency_key=operation['idempotency_key'], _recovery=True)
            else:
                await mutate_key(actor, operation['kind'].split('.', 1)[1], operation['request']['inputs'],
                                 operation['idempotency_key'], _recovery=True)
            outcomes['completed'] += 1
        except Exception:
            outcomes['pending'] += 1
    return outcomes
