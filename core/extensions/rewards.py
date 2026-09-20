"""Versioned declarative rewards applied through the existing idempotent facade."""
from collections.abc import Mapping

from core.context import AccountContext, bind_account_context
from core.results import CoreError
from database import requests as db
from . import registry


def snapshot_rewards(purpose):
    return [{'module_id': key[1], 'name': key[2], 'version': registry.MODULES[key[1]]['version'],
             'required': policy['required']}
            for key, policy in registry.policies('reward', action=purpose)]


async def apply_order_rewards(order):
    terms = db.get_payment_order_terms(order['order_id'])
    declarations = ((terms or {}).get('pricing') or {}).get('module_rewards') or []
    if not declarations:
        return
    user = db.get_user_by_id(order['user_id'])
    actor = AccountContext(user['id'], user.get('telegram_id'), terms['source'], order['order_id'])
    for declaration in declarations:
        module_id, name = declaration['module_id'], declaration['name']
        key = 'reward', module_id, name
        policy = registry.POLICIES.get(key)
        module = registry.MODULES.get(module_id)
        effect = 'module_reward_decision:' + module_id + ':' + name
        reward = db.get_payment_module_reward(order['order_id'], effect)
        if reward is None:
            if (policy is None or module is None or module['version'] != declaration['version']
                    or registry.module_state(module_id, settlement=True) != 'available'):
                if declaration['required']:
                    raise CoreError('module_unavailable', details={'module_id': module_id}, retryable=True)
                continue
            with bind_account_context(actor):
                reward = registry._sync_call(key, policy, registry.context_for({}, phase='reward', inputs={'order': order}))
            if reward is None:
                reward = {}
            if not isinstance(reward, Mapping) or set(reward) - {'kind', 'amount', 'recipient', 'reason'}:
                registry._failure(key, policy)
                continue
            if reward and (reward.get('kind') not in ('balance', 'days') or type(reward.get('amount')) is not int
                           or not 1 <= reward['amount'] <= 2**31 or reward.get('recipient', 'payer') not in ('payer', 'referrer')
                           or not isinstance(reward.get('reason'), str) or not 1 <= len(reward['reason']) <= 300):
                registry._failure(key, policy)
                continue
            # Persist the decision before an effect can change eligibility for a retry.
            reward = dict(reward)
            if reward:
                reward['target_id'] = user['id'] if reward.get('recipient', 'payer') == 'payer' else user.get('referred_by')
            reward = db.save_payment_module_reward(order['order_id'], effect, reward)
        if not reward:
            continue
        target_id = reward['target_id']
        if not target_id:
            continue
        values = {'user_id': target_id, 'reason': reward['reason'], 'source': 'module_reward',
                  'reference_type': 'payment_promo_reward',
                  'reference_id': module_id + ':' + name + ':' + order['order_id']}
        with bind_account_context(actor):
            if reward['kind'] == 'balance':
                from bot.services.balance import credit_user_balance
                result = await credit_user_balance(cents=reward['amount'], **values)
            else:
                from bot.services.rewards import grant_days_to_first_active_key
                result = await grant_days_to_first_active_key(days=reward['amount'], **values)
        if not result.get('ok') and result.get('status') != 'no_op':
            raise CoreError('module_reward_pending', retryable=True)
