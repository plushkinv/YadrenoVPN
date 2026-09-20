"""Deterministic account-aware policies; legacy registries retain their own order."""
from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType

from core.context import AccountContext, bind_account_context, get_account_context
from core.results import CoreError

MODULES: dict[str, dict] = {}
POLICIES: dict[tuple[str, str, str], dict] = {}
MANIFESTS: dict[str, dict] = {}
API_VERSION = 1


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def account_for_context(context):
    account = get_account_context()
    if account is not None:
        return account
    from bot.utils.custom_extensions import _get_current_extension_account_id
    from database import requests as db
    user_id = _get_current_extension_account_id() or context.get('user_id') or context.get('payer_id')
    user = db.get_user_by_id(int(user_id)) if user_id else None
    if user is None and context.get('telegram_id'):
        user = db.get_user_by_telegram_id(int(context['telegram_id']))
    if user is None:
        raise CoreError('authentication_required')
    source = 'telegram' if user.get('telegram_id') and context.get('source') in {'command', 'callback', 'button'} else 'system'
    return AccountContext(int(user['id']), user.get('telegram_id'), source)


def context_for(context, *, phase, inputs=None):
    account = account_for_context(context)
    return freeze({'account': {'account_id': account.account_id, 'telegram_id': account.telegram_id},
                   'source': account.source, 'operation_id': account.operation_id,
                   'phase': phase, 'capabilities': ('account', 'payments', 'subscriptions', 'storage'),
                   'inputs': dict(inputs if inputs is not None else context)})


def register_module(module_id, *, version, api_version, required_modules):
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('module version must be major.minor.patch')
    if type(api_version) is not int or api_version <= 0:
        raise ValueError('api_version must be a positive integer')
    if module_id in MODULES:
        raise ValueError('module already registered')
    if not isinstance(required_modules, (list, tuple)) or any(
            not isinstance(item, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', item) for item in required_modules):
        raise ValueError('required_modules must contain module identifiers')
    MODULES[module_id] = {'module_id': module_id, 'version': version, 'api_version': api_version,
                          'required_modules': tuple(required_modules)}


def module_state(module_id, *, settlement=False, seen=None):
    from database.requests import get_setting
    module = MODULES.get(module_id)
    if module is None:
        return 'unavailable'
    if module['api_version'] != API_VERSION:
        return 'incompatible'
    if not settlement and get_setting('core_module_enabled.' + module_id, '1') != '1':
        return 'disabled'
    seen = set(seen or ())
    if module_id in seen:
        return 'incompatible'
    seen.add(module_id)
    for dependency in module['required_modules']:
        if module_state(dependency, settlement=settlement, seen=seen) != 'available':
            return 'incompatible'
    return 'available'


def inspect_modules():
    active = [{**module, 'required_modules': list(module['required_modules']), 'state': module_state(module_id),
             'policies': [{'kind': key[0], 'name': key[2], 'priority': policy['priority'],
                           'required': policy['required'], 'actions': list(policy.get('actions', ())),
                           'eligibility': policy.get('eligibility'), 'events': list(policy.get('events', ())),
                           **{field: deepcopy(policy[field]) for field in ('input_schema', 'result_schema') if field in policy}}
                          for key, policy in sorted(POLICIES.items()) if key[1] == module_id]}
            for module_id, module in sorted(MODULES.items())]
    return sorted(active + [{**deepcopy(value), 'state': 'unavailable'} for key, value in MANIFESTS.items() if key not in MODULES],
                  key=lambda item: item['module_id'])


def load_manifests():
    from database.requests import get_core_module_manifests
    MANIFESTS.clear()
    MANIFESTS.update(get_core_module_manifests())


def persist_module(module_id):
    if module_id not in MODULES:
        return
    from database.requests import save_core_module_manifest
    manifest = next(item for item in inspect_modules() if item['module_id'] == module_id)
    save_core_module_manifest(module_id, manifest)
    MANIFESTS[module_id] = manifest


def register_policy(kind, module_id, name, handler, *, priority=0, required=True, **options):
    if module_id not in MODULES:
        raise ValueError('register_module must precede its policies')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', name):
        raise ValueError('invalid module policy name')
    if type(priority) is not int or not -10000 <= priority <= 10000 or type(required) is not bool or not callable(handler):
        raise ValueError('invalid policy declaration')
    key = kind, module_id, name
    if key in POLICIES:
        raise ValueError('module policy already registered')
    POLICIES[key] = {'handler': handler, 'priority': priority, 'required': required, **options}


def policies(kind, *, action=None, settlement=False):
    # The last successful declaration survives a restart with a missing/broken file.
    for module_id, manifest in sorted(MANIFESTS.items()):
        for declaration in manifest['policies']:
            if declaration['kind'] != kind or not declaration['required']:
                continue
            if action is not None and action not in declaration.get('actions', ()):
                continue
            if (kind, module_id, declaration['name']) not in POLICIES:
                raise CoreError('module_unavailable', details={'module_id': module_id, 'state': 'unavailable'}, retryable=True)
    items = sorted(((key, value) for key, value in POLICIES.items() if key[0] == kind),
                   key=lambda item: (item[1]['priority'], item[0][1], item[0][2]))
    for key, policy in items:
        if action is not None and action not in policy.get('actions', ()):
            continue
        state = module_state(key[1], settlement=settlement)
        if state != 'available':
            if policy['required']:
                raise CoreError('module_unavailable', details={'module_id': key[1], 'state': state}, retryable=True)
            continue
        yield key, policy


def _failure(key, policy):
    if policy['required']:
        raise CoreError('module_policy_failed', details={'module_id': key[1], 'policy': key[2]}, retryable=True) from None


def _sync_call(key, policy, context):
    from bot.utils.action_policy import _action_policy_context
    try:
        identity = context['account']
        actor = AccountContext(identity['account_id'], identity['telegram_id'], context['source'], context['operation_id'])
        with bind_account_context(actor), _action_policy_context('shared_policy'):
            result = policy['handler'](context)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError('this policy must be synchronous')
        return result
    except Exception:
        _failure(key, policy)
        return None


def first_purchase_eligibility(context):
    from database import requests as db
    account = account_for_context(context)
    stats = db.get_user_payment_snapshot_stats(account.account_id)
    imported = db.has_subscription_import(account.account_id)
    eligible = not imported and int(stats['paid_key_count']) == 0
    inputs = {'benefit': 'first_purchase', 'eligible': eligible, 'has_subscription_import': imported,
              'payment_statistics': stats}
    for key, policy in policies('eligibility'):
        result = _sync_call(key, policy, context_for(context, phase='eligibility', inputs=inputs))
        if result is None:
            continue
        if not isinstance(result, Mapping) or set(result) != {'eligible'} or type(result['eligible']) is not bool:
            _failure(key, policy)
            continue
        eligible = result['eligible']
        inputs['eligible'] = eligible
    return eligible and not db.has_other_first_purchase_reservation(account.account_id, context.get('order_id'))


def apply_pricing(quote, context):
    result = dict(quote)
    result['pricing_policies'] = list(result.get('pricing_policies') or ())
    for key, policy in policies('pricing'):
        if policy.get('eligibility') == 'first_purchase' and not first_purchase_eligibility(context):
            continue
        value = _sync_call(key, policy, context_for(context, phase=context.get('phase') or 'quote',
                                                   inputs={**context, 'quote': result}))
        if value is None:
            continue
        if not isinstance(value, Mapping) or set(value) - {'payable_amount_minor', 'allowed', 'reason'}:
            _failure(key, policy)
            continue
        if 'allowed' in value and type(value['allowed']) is not bool:
            _failure(key, policy)
            continue
        amount = value.get('payable_amount_minor', result['final_amount'])
        if type(amount) is not int or not 0 <= amount <= result['original_amount']:
            _failure(key, policy)
            continue
        previous_amount = result['final_amount']
        if value.get('allowed') is False:
            result.update(ok=False, unavailable_reason='module_policy_rejected')
        else:
            result.update(final_amount=amount, discount_amount=result['original_amount'] - amount)
        result['pricing_policies'].append({'name': key[1] + '.' + key[2], 'module_version': MODULES[key[1]]['version'],
                                           'final_amount': amount, 'eligibility': policy.get('eligibility')})
        if policy.get('eligibility') == 'first_purchase' and amount < previous_amount and result['ok']:
            result['first_purchase_benefit'] = True
        if not result['ok']:
            break
    return result


async def apply_action(action, context):
    for key, policy in policies('action', action=action):
        from bot.utils.action_policy import _action_policy_context
        try:
            with bind_account_context(account_for_context(context)), _action_policy_context('shared_action'):
                value = policy['handler'](context_for(context, phase=context['phase']))
                if inspect.isawaitable(value):
                    value = await value
            if value is None:
                continue
            if not isinstance(value, Mapping) or set(value) - {'allowed', 'reason'} or type(value.get('allowed')) is not bool:
                raise ValueError('action policy must return allowed and an optional technical reason')
            if value['allowed'] is False:
                raise CoreError('action_unavailable', details={'module_id': key[1]})
        except CoreError:
            raise
        except Exception:
            _failure(key, policy)


def remove_module(module_id):
    MODULES.pop(module_id, None)
    for key in list(POLICIES):
        if key[1] == module_id:
            POLICIES.pop(key)


def snapshot():
    return {'modules': deepcopy(MODULES), 'manifests': deepcopy(MANIFESTS),
            'policies': {key: dict(value) for key, value in POLICIES.items()}}


def restore(value):
    MODULES.clear()
    MODULES.update(value.get('modules', {}))
    POLICIES.clear()
    POLICIES.update(value.get('policies', {}))
    MANIFESTS.clear()
    MANIFESTS.update(value.get('manifests', {}))
