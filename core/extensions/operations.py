"""Declared module calls with durable pending work and bounded completed receipts."""
import asyncio
import hashlib
import inspect
import logging
import re
from dataclasses import replace

from core.accounts import require_account
from core.context import bind_account_context
from core.results import CoreError
from database import requests as db
from . import registry
from .models import json_bytes, parse_inputs, serialize_result

_locks: dict[tuple, asyncio.Lock] = {}


def _declaration(module_id, name, *, settlement=False):
    if not all(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value)
               for value in (module_id, name)):
        raise CoreError('operation_not_found')
    policy = registry.POLICIES.get(('user_operation', module_id, name))
    if policy is None or registry.module_state(module_id, settlement=settlement) != 'available':
        raise CoreError('module_unavailable', retryable=True)
    return policy


async def _execute(policy, context, parsed):
    result = (policy['handler'](context, parsed) if 'input_adapter' in policy
              else policy['handler'](context))
    if inspect.isawaitable(result):
        result = await asyncio.wait_for(result, 12)
    return serialize_result(policy, result)


def _inputs(policy, inputs):
    try:
        return parse_inputs(policy, inputs)
    except (ValueError, TypeError):
        raise CoreError('invalid_request', details={'field': 'inputs'}) from None


async def invoke_user_query(account, module_id, name, inputs):
    from runtime.readiness import require_active
    from bot.utils.action_policy import _action_policy_context
    require_active()
    require_account(account)
    policy = _declaration(module_id, name)
    if policy.get('operation_kind', 'command') != 'query':
        raise CoreError('operation_not_found')
    try:
        with bind_account_context(replace(account, operation_id=None)), _action_policy_context('module_query'):
            parsed = _inputs(policy, inputs)
            return await _execute(policy, registry.context_for({}, phase='query', inputs=inputs), parsed)
    except CoreError:
        raise
    except asyncio.TimeoutError:
        raise CoreError('module_query_timeout', retryable=True) from None
    except Exception:
        raise CoreError('module_query_failed', retryable=True) from None


def user_operation_status(account, module_id, operation_id):
    require_account(account)
    operation = db.get_module_operation_status(account.account_id, module_id, operation_id)
    if operation is None:
        raise CoreError('operation_not_found')
    state = 'result_expired' if operation.get('purged_at') is not None else (
        'completed' if operation['completed'] else 'pending')
    return {'operation_id': operation['id'], 'state': state,
            'result': operation['result'] if state == 'completed' else None,
            'error_code': operation.get('last_error_code')}


async def cleanup_operation_receipts():
    reads = [(module_id, name, version)
             for (kind, module_id, name), policy in registry.POLICIES.items()
             if kind == 'user_operation' and policy.get('operation_kind') == 'query'
             for version in policy.get('legacy_read_versions', ())]
    while db.cleanup_module_operations(reads) == 500:
        await asyncio.sleep(0)


async def invoke_user_operation(account, module_id, name, inputs, idempotency_key):
    from runtime.readiness import require_active
    require_active()
    require_account(account)
    if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency_key):
        raise CoreError('invalid_request', details={'field': 'idempotency_key'})
    if not all(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) for value in (module_id, name)):
        raise CoreError('operation_not_found')
    kind = 'module.' + module_id + '.' + name
    try:
        encoded = json_bytes(inputs)
    except (TypeError, ValueError):
        raise CoreError('invalid_request') from None
    fingerprint = hashlib.sha256(encoded).hexdigest()
    lock_key = account.account_id, kind, idempotency_key
    lock = _locks.setdefault(lock_key, asyncio.Lock())
    try:
        async with lock:
            previous = db.get_module_operation(account.account_id, kind, idempotency_key)
            if previous and previous['fingerprint'] != fingerprint:
                raise CoreError('idempotency_conflict')
            if previous and previous['completed']:
                if previous.get('purged_at') is not None:
                    raise CoreError('module_result_expired', operation_id=previous['id'])
                return {'operation_id': previous['id'], 'result': previous['result']}
            module = registry.MODULES.get(module_id)
            declaration = registry.POLICIES.get(('user_operation', module_id, name))
            if not module or not declaration:
                raise CoreError('module_unavailable', retryable=True)
            state = registry.module_state(module_id, settlement=previous is not None)
            if state != 'available' or (previous and previous['request']['version'] != module['version']):
                raise CoreError('module_unavailable', retryable=True)
            if declaration.get('operation_kind', 'command') == 'query':
                # The published POST shape remains usable without creating a receipt.
                return {'operation_id': 'query_' + fingerprint[:32],
                        'result': await invoke_user_query(account, module_id, name, inputs)}
            parsed = _inputs(declaration, inputs)
            operation = previous or db.begin_module_operation(account.account_id, kind, idempotency_key, fingerprint,
                                                              {'version': module['version'], 'source': account.source, 'inputs': inputs})
            actor = replace(account, source=operation['request']['source'], operation_id=operation['id'])
            try:
                with bind_account_context(actor):
                    context = registry.context_for({}, phase='execute', inputs=operation['request']['inputs'])
                    result = await _execute(declaration, context, parsed)
                db.finish_module_operation(account.account_id, operation['id'], result)
                return {'operation_id': operation['id'], 'result': result}
            except Exception as exc:
                code = ('handler_timeout' if isinstance(exc, asyncio.TimeoutError) else
                        'result_invalid' if isinstance(exc, (ValueError, TypeError)) else 'handler_error')
                try:
                    db.record_module_operation_error(account.account_id, operation['id'], code)
                except Exception:
                    logging.getLogger(__name__).warning('Cannot persist module error operation_id=%s', operation['id'])
                raise CoreError('module_operation_pending', retryable=True, operation_id=operation['id'],
                                details={'reason': code, 'outcome_unknown': True}) from None
    finally:
        # Queued calls retain the same lock; a new lock is safe after the last waiter.
        if not lock.locked() and not getattr(lock, '_waiters', None):
            _locks.pop(lock_key, None)
