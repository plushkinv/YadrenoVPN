"""Verified ingress and delivery over the existing extension task queue."""
import asyncio
import hashlib
import inspect
import json

from core.context import AccountContext, bind_account_context
from core.results import CoreError
from database import requests as db
from . import registry
from .models import json_bytes


def task_name(name):
    return 'webhook_' + hashlib.sha256(name.encode()).hexdigest()[:24]


def _text(value, field):
    if not isinstance(value, str) or not value or len(value) > 256 or '\x00' in value:
        raise CoreError('invalid_request', details={'field': field})
    return value


def bind_reference(account, module_id, name, reference):
    from core.accounts import require_account
    from bot.utils.extension_core import _ensure_new_mutation_allowed
    _ensure_new_mutation_allowed('bind_webhook_reference')
    require_account(account)
    if ('webhook', module_id, name) not in registry.POLICIES:
        raise CoreError('webhook_not_found')
    db.bind_module_webhook_reference(module_id, name, _text(reference, 'reference'), account.account_id)


async def accept(module_id, name, raw_body, headers):
    from bot.utils.action_policy import _action_policy_context
    policy = registry.POLICIES.get(('webhook', module_id, name))
    module = registry.MODULES.get(module_id)
    if not policy or not module or registry.module_state(module_id, settlement=True) != 'available':
        raise CoreError('webhook_not_found')
    try:
        with bind_account_context(None), _action_policy_context('webhook_verification'):
            event = policy['verifier'](raw_body, headers)
            if inspect.isawaitable(event):
                event = await asyncio.wait_for(event, 12)
    except Exception:
        raise CoreError('webhook_authentication_failed') from None
    if not isinstance(event, dict):
        raise CoreError('webhook_authentication_failed')
    if set(event) - {'event_id', 'payload', 'reference'}:
        raise CoreError('invalid_request')
    event_id = _text(event.get('event_id'), 'event_id')
    reference = event.get('reference')
    if reference is not None:
        reference = _text(reference, 'reference')
    payload = event.get('payload', {})
    if not isinstance(payload, dict):
        raise CoreError('invalid_request', details={'field': 'payload'})
    try:
        fingerprint = hashlib.sha256(json_bytes({'payload': payload, 'reference': reference})).hexdigest()
    except (ValueError, TypeError):
        raise CoreError('invalid_request') from None
    return db.accept_module_webhook(module_id, name, module['version'], task_name(name), event_id,
        fingerprint, payload, reference, enabled=registry.module_state(module_id) == 'available')


async def dispatch(module_id, name, context):
    event = db.get_module_webhook(module_id, context['payload']['webhook_id'])
    policy = registry.POLICIES.get(('webhook', module_id, name))
    module = registry.MODULES.get(module_id)
    if (not event or not policy or not module or event['name'] != name or event['task_id'] != context['task_id']
            or event['version'] != module['version'] or registry.module_state(module_id, settlement=True) != 'available'):
        raise LookupError('webhook handler is unavailable')
    actor = (AccountContext(context['user_id'], context.get('telegram_id'), 'system', event['id'])
             if context.get('user_id') else None)
    value = registry.freeze({'event_id': event['event_id'], 'operation_id': event['id'],
        'delivery_attempt': context['delivery_attempt'], 'payload': json.loads(event['payload_json']),
        'account': {'account_id': actor.account_id, 'telegram_id': actor.telegram_id} if actor else None})
    with bind_account_context(actor):
        result = policy['handler'](value)
        if inspect.isawaitable(result):
            result = await result
        return result
