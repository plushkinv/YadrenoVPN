"""Catalog projection uses the native pricing and action-policy pipeline."""
from core.accounts import owned_key, require_account
from core.context import bind_account_context
from database import requests as db


async def catalog(account, *, key_id=None):
    from bot.services.payment_pricing import preview_tariff_price
    from bot.utils.groups import get_tariffs_for_key_renewal
    from bot.utils.action_policy import run_account_action_policies
    from core.payment_offers import available_methods, offer_intent
    require_account(account)
    key = owned_key(account, key_id) if key_id is not None else None
    purpose = 'key_renewal' if key is not None else 'key_purchase'
    action = 'key.renew.start' if key is not None else 'key.purchase.start'
    tariffs = get_tariffs_for_key_renewal(key) if key else db.get_all_tariffs(include_hidden=False)
    groups = {group['id']: group for group in db.get_all_groups()}
    group_has_servers = {group_id: bool(db.get_active_servers_by_group(group_id)) for group_id in groups}
    result = []
    with bind_account_context(account):
        for tariff in tariffs:
            if tariff.get('system_type') is not None or not tariff.get('is_active'):
                continue
            if not key and len(groups) > 1 and not group_has_servers.get(tariff['group_id']):
                continue
            params = {'key_id': key_id} if key else {'tariff_id': tariff['id']}
            decision = await run_account_action_policies(action, params, account=account, phase='preview')
            price = preview_tariff_price(user_id=account.account_id, tariff=tariff, purpose=purpose, key_id=key_id)
            item = {name: tariff.get(name) for name in ('id', 'name', 'group_id', 'duration_days', 'traffic_limit_gb', 'max_ips')}
            methods = available_methods(account, offer_intent(account, {'purpose': purpose, 'tariff_id': tariff['id'], 'key_id': key_id}, tariff))
            item.update(base_currency=price['base_currency'], nominal_amount_minor=price['nominal_amount_minor'],
                        payable_amount_minor=price['payable_amount_minor'], payment_methods=methods,
                        available=bool(price['ok'] and decision['decision'] == 'continue' and methods),
                        reason=price.get('unavailable_reason') or ('action_unavailable' if decision['decision'] != 'continue'
                               else None if methods else 'payment_method_unavailable'))
            result.append(item)
    return {'purpose': purpose, 'subscription_id': key_id, 'tariffs': result,
            'groups': [{'id': value['id'], 'name': value['name']} for value in groups.values()]}
