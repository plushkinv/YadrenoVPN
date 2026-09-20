"""Account-owned trial activation and provisioning without Telegram delivery."""
from dataclasses import replace

from core.accounts import require_account
from core.context import bind_account_context
from core.operations import operation_fingerprint, positive_id
from core.results import CoreError
from database import requests as db


async def list_offers(account):
    require_account(account)
    from bot.utils.action_policy import run_account_action_policies
    result = []
    with bind_account_context(account):
        for offer in db.get_all_trial_offers():
            if not offer['is_enabled']:
                continue
            eligibility = db.get_account_trial_eligibility(account.account_id, offer['offer_id'])
            if eligibility['eligible']:
                decision = await run_account_action_policies('trial.activate', {'offer_id': offer['offer_id']},
                                                            account=account, phase='preview')
                if decision['decision'] != 'continue':
                    eligibility = {**eligibility, 'eligible': False, 'reason': 'action_unavailable'}
            result.append(eligibility)
    return result


async def activate(account, offer_id, idempotency_key):
    require_account(account)
    positive_id(offer_id, 'offer_id')
    fingerprint = operation_fingerprint(idempotency_key, {'offer_id': offer_id})
    from runtime.readiness import require_active
    from bot.services.user_locks import user_locks
    from bot.utils.action_policy import run_account_action_policies
    from bot.utils.extension_event_registry import event_subscribers
    require_active()
    with bind_account_context(account):
        async with user_locks[account.account_id]:
            previous = db.get_module_operation(account.account_id, 'trial.activate', idempotency_key)
            if not previous:
                decision = await run_account_action_policies('trial.activate', {'offer_id': offer_id}, account=account, phase='execute')
                if decision['decision'] != 'continue':
                    raise CoreError('action_unavailable')
            receipt = db.claim_trial_offer_once(account.account_id, offer_id, idempotency_key, fingerprint, account.source,
                                                event_subscribers=event_subscribers('trial.activated'))
        result = receipt['result']
        if not result['ok']:
            return {'ok': False, 'reason': result['reason'], 'scope': result.get('scope'),
                    'operation_id': receipt['operation_id']}
        actor = replace(account, operation_id=receipt['operation_id'])
        with bind_account_context(actor):
            if receipt['applied']:
                from bot.services.trials import emit_trial_key_created
                await emit_trial_key_created(account.account_id, offer_id, result)
                from runtime.delivery import get_bot
                from bot.services.payment_fulfillment import _notify_admins_once
                await _notify_admins_once(result['order_id'], bot=get_bot())
            from bot.services.new_key_setup import resolve_new_key_setup, provision_new_key, NewKeySetupStatus
            setup = await resolve_new_key_setup(result['order_id'], expected_telegram_id=account.telegram_id)
            if setup.status is NewKeySetupStatus.PROVISIONING:
                setup = await provision_new_key(setup, expected_telegram_id=account.telegram_id)
            composition = None
            if setup.status is NewKeySetupStatus.READY:
                from bot.services.subscription_host_flow import resolve_default_host
                composition, hosts = await resolve_default_host(result['key_id'])
            return {'ok': True, 'operation_id': receipt['operation_id'], 'key_id': result['key_id'],
                    'order_id': result['order_id'], 'state': setup.status.value,
                    'server_ids': [server['id'] for server in setup.servers], 'composition': composition}


async def recover_trial_access(limit=25):
    """Resume an accepted trial even if the caller disappeared before setup started."""
    from bot.services.new_key_setup import resolve_new_key_setup, provision_new_key, NewKeySetupStatus
    from core.context import AccountContext
    cursor = int(db.get_setting('web_trial_recovery_cursor', '0') or 0)
    rows = db.get_pending_trial_access(after_key_id=cursor, limit=limit)
    if not rows and cursor:
        rows = db.get_pending_trial_access(limit=limit)
    result = {'ready': 0, 'pending': 0}
    for row in rows:
        db.set_setting('web_trial_recovery_cursor', str(row['key_id']))
        user = db.get_user_by_id(row['user_id'])
        if not user or user['is_banned']:
            result['pending'] += 1
            continue
        with bind_account_context(AccountContext(user['id'], user.get('telegram_id'), 'system')):
            try:
                setup = await resolve_new_key_setup(row['order_id'])
                if setup.status is NewKeySetupStatus.PROVISIONING:
                    setup = await provision_new_key(setup)
                if setup.status is not NewKeySetupStatus.READY:
                    result['pending'] += 1
                    continue
                entitlement = db.get_key_entitlement(row['key_id'])
                if entitlement and not entitlement['panel_applied']:
                    from bot.services.vpn_api import sync_key_to_panel_state
                    synced = await sync_key_to_panel_state(row['key_id'])
                    if not synced.get('ok') or synced.get('errors'):
                        result['pending'] += 1
                        continue
                    if not db.complete_key_entitlement_sync(row['key_id'], row['order_id']):
                        result['pending'] += 1
                        continue
                from bot.services.subscription_host_flow import resolve_default_host
                await resolve_default_host(row['key_id'])
                result['ready'] += 1
            except Exception:
                result['pending'] += 1
    return result
