"""Shared subscribers reuse the committed core-event outbox and delivery worker."""
import hashlib
import inspect

from core.context import AccountContext, bind_account_context
from database import requests as db
from . import registry


def handler_token(module_id, name):
    version = registry.MODULES[module_id]['version']
    # ':' cannot occur in a published legacy completion-handler identifier.
    return 'core:' + hashlib.sha256((name + ':' + version).encode()).hexdigest()[:27]


def subscribers(event_name):
    result = []
    for key, policy in sorted(registry.POLICIES.items(), key=lambda item: (item[1]['priority'], item[0])):
        if key[0] != 'event' or event_name not in policy['events']:
            continue
        if registry.module_state(key[1]) != 'available':
            continue
        result.append({'extension_id': key[1], 'handler_name': handler_token(key[1], key[2]), 'shared_module': True})
    return result


def snapshot_payment_events():
    captured = {event: subscribers(event) for event in ('payment.completed', 'key.delivered')}
    return {event: values for event, values in captured.items() if values}


def subscribers_for_order(event, order_id):
    from bot.utils.extension_event_registry import event_subscribers
    live = event_subscribers(event)
    terms = db.get_payment_order_terms(order_id)
    pricing = (terms or {}).get('pricing') or {}
    if 'module_events' not in pricing:
        return live
    return [value for value in live if not value.get('shared_module')] + pricing['module_events'].get(event, [])


async def dispatch(job):
    for key, policy in registry.POLICIES.items():
        if key[0] != 'event' or key[1] != job['extension_id']:
            continue
        token = handler_token(key[1], key[2])
        if job['handler_name'] not in (token, token.replace('core:', 'core_', 1)):
            continue
        if registry.module_state(key[1], settlement=True) != 'available' or job['event_name'] not in policy['events']:
            raise LookupError('shared event subscriber unavailable')
        payload = job['payload']
        user = db.get_user_by_id(payload['user_id'])
        if not user:
            raise LookupError('event account unavailable')
        actor = AccountContext(user['id'], user.get('telegram_id'), 'system', payload['event_id'])
        with bind_account_context(actor):
            result = policy['handler'](registry.context_for({}, phase='event', inputs={
                'event': payload, 'delivery_attempt': job['attempts']}))
            if inspect.isawaitable(result):
                result = await result
        from bot.utils.extension_completion_registry import normalize_extension_completion_result
        return normalize_extension_completion_result(result)
    raise LookupError('shared event subscriber unavailable')


async def emit_lifecycle(event, context):
    outcomes = []
    for key, policy in sorted(registry.POLICIES.items(), key=lambda item: (item[1]['priority'], item[0])):
        if key[0] != 'lifecycle' or event not in policy['events'] or registry.module_state(key[1]) != 'available':
            continue
        name = key[1] + '.' + key[2]
        try:
            actor_context = dict(context)
            if not actor_context.get('user_id') and actor_context.get('key_id'):
                identity = db.get_extension_key_user_identity(actor_context['key_id'])
                actor_context['user_id'] = (identity or {}).get('user_id')
            with bind_account_context(registry.account_for_context(actor_context)):
                result = policy['handler'](registry.context_for({}, phase='lifecycle', inputs={**context, 'event': event}))
                if inspect.isawaitable(result):
                    result = await result
            from bot.utils.lifecycle_registry import _normalize_hook_result
            outcomes.append({'name': name, **_normalize_hook_result(result)})
        except Exception:
            outcomes.append({'name': name, 'ok': False, 'reason': 'module_lifecycle_failed'})
    return outcomes
